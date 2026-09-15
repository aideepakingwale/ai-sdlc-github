"""Brownfield support: users upload an existing codebase (zip); text
sources are stored per-project and indexed into the RAG corpus so every phase
agent grounds its designs and code changes in the real code."""

from __future__ import annotations

import io
import zipfile

from ..domain.errors import SdlcError
from ..repos.pg import Database
from .rag import RagService

TEXT_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".java", ".kt", ".cs", ".go", ".rb",
    ".php", ".rs", ".sql", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini", ".env",
    ".css", ".scss", ".html", ".sh", ".ps1", ".tf", ".proto", ".graphql", ".xml", ".gradle",
}
SKIP_DIR_MARKERS = (
    "node_modules/", ".git/", "dist/", "build/", "target/", ".venv/", "venv/",
    "__pycache__/", ".next/", "coverage/", "vendor/", ".idea/", ".vscode/",
)
MAX_FILES = 400
MAX_FILE_BYTES = 200_000
MAX_ZIP_BYTES = 25 * 1024 * 1024


def extract_text_files(zip_bytes: bytes) -> list[tuple[str, str]]:
    """Deterministic, bounded extraction: (path, content) for indexable sources."""
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise SdlcError("VALIDATION_FAILED", f"Zip exceeds {MAX_ZIP_BYTES // (1024 * 1024)}MB limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as err:
        raise SdlcError("VALIDATION_FAILED", "Upload is not a valid zip archive") from err

    files: list[tuple[str, str]] = []
    for info in archive.infolist():
        if info.is_dir() or len(files) >= MAX_FILES:
            continue
        path = info.filename.replace("\\", "/").lstrip("/")
        if ".." in path or any(marker in path for marker in SKIP_DIR_MARKERS):
            continue
        dot = path.rfind(".")
        if dot < 0 or path[dot:].lower() not in TEXT_EXTENSIONS:
            continue
        if info.file_size > MAX_FILE_BYTES:
            continue
        try:
            content = archive.read(info).decode("utf-8")
        except (UnicodeDecodeError, zipfile.BadZipFile):
            continue
        if content.strip():
            files.append((path, content))
    if not files:
        raise SdlcError("VALIDATION_FAILED", "No indexable text source files found in the zip")
    return files


class CodebaseService:
    def __init__(self, db: Database, rag: RagService) -> None:
        self._db = db
        self._rag = rag

    async def ingest_zip(self, project_id: str, uploaded_by: str, zip_bytes: bytes) -> dict:
        files = extract_text_files(zip_bytes)
        for path, content in files:
            await self._db.upsert_codebase_file(
                project_id=project_id, path=path, content=content, uploaded_by=uploaded_by
            )
            await self._rag.index_codebase_file(project_id, path, content)
        return {"files": len(files), "paths": [p for p, _ in files[:50]]}
