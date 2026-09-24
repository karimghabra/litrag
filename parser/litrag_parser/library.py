"""A library: one project's papers, in a folder under the root.

The root is `LITRAG_ROOT`, else `PROTRACKER_LIBRARY`, else `~/.protracker/library`,
so the desktop and the notebook agree on one place. A library is a manifest, a
store, the papers themselves, the raw parses beside them, and an inbox.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def library_root(env: dict[str, str] | None = None) -> Path:
    e = env if env is not None else dict(os.environ)
    return Path(e.get("LITRAG_ROOT") or e.get("PROTRACKER_LIBRARY") or Path.home() / ".protracker" / "library")


def safe_key(key: str) -> str:
    """A paper key as a file name: `doi:10.1/abc` → `doi_10.1_abc`."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key)


def parsed_papers(lib_dir: Path, keys: list[str] | None = None) -> list[dict[str, Any]]:
    """Every parsed paper of a library that still has its raw Docling document:
    `{key, format, file, source, raw, pub_types, type, journal}`, in the order they were added.
    `source` is the paper file (or None), `raw` the saved document beside it; `journal` is what the
    record knows, which the reader uses to tell a banner from a heading."""
    import sqlite3

    store = Path(lib_dir) / "store.sqlite"
    if not store.exists():
        return []
    conn = sqlite3.connect(store)
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
        extra = ", pub_types, type" if "pub_types" in have else ", NULL, NULL"
        extra += ", journal" if "journal" in have else ", NULL"
        rows = conn.execute(f"SELECT key, format, file{extra} FROM papers WHERE status = 'parsed' ORDER BY added_at, key").fetchall()
    finally:
        conn.close()
    wanted = set(keys) if keys else None
    out: list[dict[str, Any]] = []
    for key, fmt, file, pub_types, kind, journal in rows:
        if wanted is not None and key not in wanted:
            continue
        raw = Path(lib_dir) / "parsed" / f"{safe_key(key)}.docling.json"
        if not raw.exists():
            continue
        out.append({"key": key, "format": fmt, "file": file, "source": (Path(lib_dir) / "papers" / file) if file else None, "raw": raw, "pub_types": pub_types, "type": kind, "journal": journal})
    return out


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


_MANIFEST_LOCK = threading.RLock()


def update_manifest(lib: Library, change: Any) -> dict[str, Any]:
    """Read library.json fresh, let `change` edit it, write it back the way `create_library` does.

    One writer at a time: a search (its own thread), a description (the stdin thread) and a merge
    (the ingest thread) each read, change and write the manifest, and two at once lost one's
    change — or, on Windows, had `os.replace` refused while the other held the file open. So one
    lock, a temp file of its own per write, and a few tries at the replace."""
    with _MANIFEST_LOCK:
        try:
            manifest = json.loads(lib.manifest_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = dict(lib.manifest)
        change(manifest)
        tmp = lib.manifest_path.with_name(f"library.json.{uuid.uuid4().hex[:8]}.part")
        tmp.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + "\n", "utf-8")
        for attempt in range(6):
            try:
                os.replace(tmp, lib.manifest_path)
                break
            except PermissionError:
                if attempt == 5:
                    tmp.unlink(missing_ok=True)
                    raise
                time.sleep(0.05 * (attempt + 1))
        lib.manifest = manifest
        return manifest


def queries_of(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """A manifest's searches as `{query, at?, total?, added?}`: a library from before the studio
    kept each as its bare query string."""
    return [q if isinstance(q, dict) else {"query": str(q)} for q in manifest.get("queries") or []]


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
    """By id first, then by name, then by Protracker project id — so a project renamed to
    another's id can never capture that one's requests."""
    wanted = key.strip().lower()
    libs = list_libraries(root)
    for match in (lambda l: l.id == wanted, lambda l: l.manifest.get("name", "").lower() == wanted,
                  lambda l: str(l.manifest.get("projectId", "")).lower() == wanted):
        for lib in libs:
            if match(lib):
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
