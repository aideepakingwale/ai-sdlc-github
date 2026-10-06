"""Rich document ingestion: text, tables, pictures, diagrams and slides from any
common file type, as structured Markdown. See `ingest.ingest`."""
from .fit import allocate, fit_document
from .ingest import SUPPORTED_DESCRIPTION, ingest, parse
from .model import Figure, IngestError, Limits, RichDocument

__all__ = ["Figure", "IngestError", "Limits", "RichDocument", "SUPPORTED_DESCRIPTION",
           "allocate", "fit_document", "ingest", "parse"]
