"""Managing the organisation's rule packs: create, import, edit, export, hide a built-in sample, or restore it.

Administrators only. A pack saved here with the id of a built-in sample replaces it for every project; hiding a built-in saves an inactive
row; deleting the row restores the sample. Saving bumps the version, which lets projects see that a newer set of rules is available.
"""
from __future__ import annotations

import json
from typing import Any

import yaml

from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from .rule_packs import KIND_LABEL, PackCatalog, normalise_pack, resolve_entries, builtin_packs


class PackAdmin:
    def __init__(self, db: Any, audit: Any, catalog: PackCatalog) -> None:
        self._db, self._audit, self._catalog = db, audit, catalog

    @staticmethod
    def _admin(user: UserPublic) -> None:
        if user.role != "SUPER_ADMIN":
            raise SdlcError("FORBIDDEN", "Only an administrator can change the organisation's rule packs")

    async def list(self) -> list[dict[str, Any]]:
        """Every pack with where it comes from (built-in, organisation, customised, hidden) and how many rules it stands for."""
        cat = await self._catalog.all(include_hidden=True)
        out = []
        for p in cat.values():
            try:
                total = len(resolve_entries({k: v for k, v in cat.items() if v.get("active", True)}, p["id"])) if p.get("active", True) else 0
            except Exception:  # noqa: BLE001 - a broken include shows as 0 rather than breaking the list
                total = 0
            built = any(b["id"] == p["id"] for b in builtin_packs())
            out.append({"id": p["id"], "name": p["name"], "description": p["description"], "kind": p["kind"], "kindLabel": KIND_LABEL[p["kind"]],
                        "version": p["version"], "baseline": p["baseline"], "tags": p["tags"], "includes": p["includes"], "rules": total,
                        "source": p.get("source", "builtin"), "hidden": not p.get("active", True), "canRestore": built and p.get("source") == "customised"})
        return sorted(out, key=lambda x: (x["kind"] != "bundle", x["kind"], x["name"]))

    async def get(self, pack_id: str) -> dict[str, Any]:
        cat = await self._catalog.all(include_hidden=True)
        if pack_id not in cat:
            raise SdlcError("NOT_FOUND", "Pack not found")
        return cat[pack_id]

    async def save(self, user: UserPublic, data: dict[str, Any]) -> dict[str, Any]:
        """Create or replace a pack from a dict; the version goes up by one when an existing pack changes."""
        self._admin(user)
        cat = await self._catalog.all(include_hidden=True)
        prior = cat.get(str(data.get("id") or ""))
        try:
            pack = normalise_pack({**data, "version": (prior["version"] + 1) if prior else int(data.get("version") or 1)},
                                  known_ids={k for k, v in cat.items() if v.get("active", True)})
        except ValueError as err:
            raise SdlcError("VALIDATION_FAILED", str(err)) from err
        trial = {k: v for k, v in cat.items() if v.get("active", True)}
        trial[pack["id"]] = pack
        try:
            resolve_entries(trial, pack["id"])          # a loop between sets is refused here
        except Exception as err:  # noqa: BLE001
            raise SdlcError("VALIDATION_FAILED", str(err)) from err
        await self._db.upsert_org_pack({**pack, "active": True}, user.id)
        self._audit.record(project_id=None, agent_role="Canon", event="rule_pack.saved", human_reviewer=user.email,
                           detail={"pack": pack["id"], "version": pack["version"], "kind": pack["kind"], "rules": len(pack["entries"])})
        return {**pack, "source": "customised" if prior and prior.get("source") != "org" else "org"}

    async def import_text(self, user: UserPublic, text: str) -> dict[str, Any]:
        """A pack as YAML or JSON text (a file someone exported or wrote)."""
        self._admin(user)
        try:
            data = yaml.safe_load(text) if not text.lstrip().startswith("{") else json.loads(text)
        except Exception as err:  # noqa: BLE001
            raise SdlcError("VALIDATION_FAILED", f"That is not valid YAML or JSON: {str(err)[:160]}") from err
        if not isinstance(data, dict):
            raise SdlcError("VALIDATION_FAILED", "A pack file is one mapping with an id, a name, a kind and its rules or included packs")
        return await self.save(user, data)

    async def export_text(self, pack_id: str) -> str:
        p = await self.get(pack_id)
        keep = {k: p[k] for k in ("id", "name", "description", "kind", "version", "baseline", "tags", "includes", "entries") if k in p}
        if not keep.get("includes"):
            keep.pop("includes", None)
        if not keep.get("entries"):
            keep.pop("entries", None)
        return yaml.safe_dump(keep, sort_keys=False, allow_unicode=True, width=120)

    async def hide(self, user: UserPublic, pack_id: str) -> None:
        self._admin(user)
        p = await self.get(pack_id)
        await self._db.upsert_org_pack({**{k: p[k] for k in ("id", "name", "description", "kind", "version", "baseline", "tags", "includes", "entries")}, "active": False}, user.id)
        self._audit.record(project_id=None, agent_role="Canon", event="rule_pack.hidden", human_reviewer=user.email, detail={"pack": pack_id})

    async def delete_or_restore(self, user: UserPublic, pack_id: str) -> str:
        """Remove an organisation pack, or restore the built-in sample it replaced or hid."""
        self._admin(user)
        if not await self._db.delete_org_pack(pack_id):
            raise SdlcError("NOT_FOUND", "There is no organisation pack with that id")
        restored = any(b["id"] == pack_id for b in builtin_packs())
        self._audit.record(project_id=None, agent_role="Canon", event="rule_pack.restored" if restored else "rule_pack.deleted", human_reviewer=user.email, detail={"pack": pack_id})
        return "restored" if restored else "deleted"
