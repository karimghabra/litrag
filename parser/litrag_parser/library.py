"""A library: one project's papers, in a folder under the root.

The root is `LITRAG_ROOT`, else `PROTRACKER_LIBRARY`, else `~/.protracker/library`,
so the desktop and the notebook agree on one place. A library is a manifest, a
store, the papers themselves, the raw parses beside them, and an inbox.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def library_root(env: dict[str, str] | None = None) -> Path:
    e = env if env is not None else dict(os.environ)
    return Path(e.get("LITRAG_ROOT") or e.get("PROTRACKER_LIBRARY") or Path.home() / ".protracker" / "library")


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60]
    return s or "library"


@dataclass
class Library:
    root: Path
    id: str
    manifest: dict[str, Any]

    @property
    def dir(self) -> Path:
        return self.root / self.id

    @property
    def manifest_path(self) -> Path:
        return self.dir / "library.json"

    @property
    def store_path(self) -> Path:
        return self.dir / "store.sqlite"

    @property
    def papers_dir(self) -> Path:
        return self.dir / "papers"

    @property
    def parsed_dir(self) -> Path:
        return self.dir / "parsed"

    @property
    def inbox_dir(self) -> Path:
        return self.dir / "inbox"

    def ensure_dirs(self) -> None:
        for d in (self.papers_dir, self.parsed_dir, self.inbox_dir):
            d.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.manifest.get("name", self.id), "dir": str(self.dir), "projectId": self.manifest.get("projectId"), "createdAt": self.manifest.get("createdAt")}


def list_libraries(root: Path) -> list[Library]:
    if not root.exists():
        return []
    out: list[Library] = []
    for entry in sorted(root.iterdir()):
        m = entry / "library.json"
        if entry.is_dir() and m.exists():
            try:
                manifest = json.loads(m.read_text("utf-8"))
            except json.JSONDecodeError:
                continue
            if manifest.get("id") and manifest.get("name"):
                out.append(Library(root=root, id=entry.name, manifest=manifest))
    return sorted(out, key=lambda l: l.manifest.get("name", l.id).lower())


def open_library(root: Path, key: str) -> Library | None:
    wanted = key.strip().lower()
    for lib in list_libraries(root):
        if lib.id == wanted or lib.manifest.get("name", "").lower() == wanted or str(lib.manifest.get("projectId", "")).lower() == wanted:
            return lib
    return None


def create_library(root: Path, name: str, project_id: str | None = None) -> Library:
    lib_id = slugify(name)
    if open_library(root, lib_id) is not None:
        raise ValueError(f"A library with that id already exists: {lib_id}")
    manifest: dict[str, Any] = {"id": lib_id, "name": name.strip(), "createdAt": now_iso(), "includes": [], "queries": []}
    if project_id:
        manifest["projectId"] = project_id
    lib = Library(root=root, id=lib_id, manifest=manifest)
    lib.ensure_dirs()
    lib.manifest_path.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + "\n", "utf-8")
    return lib
