"""Moving libraries of the old shape into the shared store.

Before the shared store each project kept its own `store.sqlite`, `papers/` and `parsed/` in its
folder, so a paper two projects held was filed, read and embedded twice. The shared store
(`<root>/_shared`, members.py) keeps every paper once, by its identifier, and a project is the list
of papers it holds. This module moves an old library across:

- **Nothing is read again.** A paper new to the shared store brings its row and every row derived
  from it as they are — its pages, its tree (`nodes`, and `nodes_fts` through its trigger), its
  reference list and citations, its edges, the model's stored answers (`outlines`, `judgments`),
  the labels a person or the model gave (`link_labels`, `model_labels`), its history (`events`),
  its charts and what each figure came to, and its passages' vectors — and its files: the paper,
  the PDF kept beside an XML paper for its figures (and any set aside), and its raw Docling
  document. Docling, the embedder and the network are never asked.
- **A paper is stored once.** One the shared store holds already — by key, else by DOI, PMID or
  file hash, as `store.find_paper` meets a paper anywhere — stays as it was read (invariant 4):
  the project holds it, and only the labels a person gave it here come with it, moved to its key.
- **Candidates** (a search's hits, a round's works) are kept once by identifier too, their status
  as it was; the project keeps the ones it had (`project_candidates`), and the round's `cites`,
  `harvests` and `ref_lists` come with them, candidate ids moved to the shared store's.
- **The source is never touched.** It is opened read-only and its files are copied, never moved;
  its manifest alone changes, to say `"store": "shared"` and when and from where it moved, so the
  window reads the project from the shared store from then on. `<root>/<id>/store.sqlite` and its
  files stay where they are; deleting them is a person's decision, never this module's.
- **A backup first.** Before anything is written, the shared store and each source's store and
  manifest are copied (SQLite's own backup, consistent while open) into
  `<root>/_backups/<time>/`.
- **Idempotent.** A library whose manifest says it moved is skipped; a move interrupted half way
  runs again to the same end, since every paper and candidate already copied is met by its key.

`python -m litrag_parser.migrate [--root DIR] [--lib ID]... [--dry-run] [--json]`; the worker's
`migrate` op does the same, and the window offers it when a library of the old shape is found.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

from . import acquire, graph, members
from .library import Library, library_root, list_libraries, now_iso, safe_key, shared_dir, update_manifest
from .store import find_paper, open_store

#: The tables whose rows belong to one paper, by their `paper` column, copied with it in this
#: order (a row's foreign key names its paper, which is copied first). `vectors` belong to a paper
#: through their node, and are copied apart.
PAPER_TABLES = ("pages", "nodes", "refs", "citations", "outlines", "judgments", "edges", "link_labels", "model_labels",
                "events", "charts", "chart_values", "figure_outcomes", "figure_reads", "ref_lists")

#: Derived again by `graph.sync` from the rows above, so never copied: a copy would carry the
#: source's candidate ids.
DERIVED = ("ref_works", "authors", "graph_state")


def _read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def legacy(root: Path, only: list[str] | None = None) -> list[Library]:
    """The libraries that keep their own store: every project not moved yet, or those of `only`."""
    want = {o.strip().lower() for o in only} if only else None
    out = []
    for lib in list_libraries(Path(root)):
        if lib.shared:
            continue
        if want is not None and lib.id not in want and str(lib.manifest.get("name", "")).lower() not in want:
            continue
        out.append(lib)
    return out


def _held(tconn: sqlite3.Connection | None, row: dict[str, Any]) -> str | None:
    """The key the shared store holds this paper by, if it does."""
    if tconn is None:
        return None
    if tconn.execute("SELECT 1 FROM papers WHERE key = ?", (row["key"],)).fetchone():
        return str(row["key"])
    hit = find_paper(tconn, doi=row.get("doi"), pmid=row.get("pmid"), sha256=row.get("sha256"))
    return hit[0] if hit else None


def plan(root: Path, only: list[str] | None = None) -> dict[str, Any]:
    """What a move would do, writing nothing: `{shared, libraries: [{id, name, papers, new, held,
    candidates, vectors, files, bytes}], totals}` — `new` the papers the shared store would gain,
    `held` those it holds already (the project would hold them as they were read there), `files`
    and `bytes` what would be copied."""
    root = Path(root)
    store = shared_dir(root) / "store.sqlite"
    tconn = _read_only(store) if store.exists() else None
    seen: dict[str, str] = {}  # papers new to the store from an earlier library in this same plan
    libs = []
    try:
        for lib in legacy(root, only):
            entry: dict[str, Any] = {"id": lib.id, "name": lib.manifest.get("name", lib.id), "papers": 0, "new": 0, "held": 0, "candidates": 0, "vectors": 0, "files": 0, "bytes": 0}
            if lib.store_path.exists():
                sconn = _read_only(lib.store_path)
                try:
                    have = _tables(sconn)
                    if "papers" in have:
                        for r in sconn.execute("SELECT * FROM papers ORDER BY added_at, key"):
                            row = dict(r)
                            entry["papers"] += 1
                            ident = [str(row["key"]), *(f"{k}:{str(row[k]).lower()}" for k in ("doi", "pmid", "sha256") if row.get(k))]
                            if _held(tconn, row) or any(i in seen for i in ident):
                                entry["held"] += 1
                                continue
                            entry["new"] += 1
                            seen.update(dict.fromkeys(ident, lib.id))
                            for f in _files_of(lib, row):
                                entry["files"] += 1
                                entry["bytes"] += f.stat().st_size
                    if "candidates" in have:
                        entry["candidates"] = sconn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
                    if "vectors" in have:
                        entry["vectors"] = sconn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
                finally:
                    sconn.close()
            libs.append(entry)
    finally:
        if tconn is not None:
            tconn.close()
    totals = {k: sum(e[k] for e in libs) for k in ("papers", "new", "held", "candidates", "vectors", "files", "bytes")}
    return {"shared": str(shared_dir(root)), "libraries": libs, "totals": totals}


def _files_of(lib: Library, row: dict[str, Any]) -> list[Path]:
    """A paper's files in an old library: under `papers/` every file named for its key — the paper
    itself, the PDF kept beside its XML, any set aside — and its raw Docling document."""
    safe = safe_key(str(row["key"]))
    out: list[Path] = []
    named = {row.get("file"), row.get("figures_file")} - {None, ""}
    if lib.papers_dir.is_dir():
        for f in sorted(lib.papers_dir.iterdir()):
            if f.is_file() and (f.name in named or f.name.startswith(safe + ".")):
                out.append(f)
    raw = lib.parsed_dir / f"{safe}.docling.json"
    if raw.is_file():
        out.append(raw)
    return out


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)


def _backup_db(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)
    d = sqlite3.connect(dest)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def backup(root: Path, libs: list[Library], at: str) -> Path:
    """The shared store as it is, and each library's store and manifest, copied into
    `<root>/_backups/<at>/` before a move writes anything. Returns the folder."""
    stamp = at.replace(":", "").replace("-", "")
    folder = Path(root) / "_backups" / stamp
    n = 1
    while folder.exists():
        n += 1
        folder = Path(root) / "_backups" / f"{stamp}-{n}"
    folder.mkdir(parents=True)
    store = shared_dir(root) / "store.sqlite"
    if store.exists():
        _backup_db(store, folder / "_shared" / "store.sqlite")
    for lib in libs:
        if lib.store_path.exists():
            _backup_db(lib.store_path, folder / lib.id / "store.sqlite")
        if lib.manifest_path.exists():
            _copy(lib.manifest_path, folder / lib.id / "library.json")
    return folder


def _insert(tconn: sqlite3.Connection, table: str, cols: list[str], rows: list[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    cur = tconn.executemany(f"INSERT OR IGNORE INTO {table}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
    return cur.rowcount


def _copy_paper(lib: Library, sconn: sqlite3.Connection, s_tables: set[str], tconn: sqlite3.Connection, row: dict[str, Any], now: str) -> dict[str, int]:
    """A paper new to the shared store, with every row derived from it and its files."""
    key = str(row["key"])
    shared = Library(root=lib.root, id="_shared", manifest={}, store_dir=shared_dir(lib.root))
    files = 0
    for f in _files_of(lib, row):
        dest = (shared.parsed_dir if f.parent == lib.parsed_dir else shared.papers_dir) / f.name
        _copy(f, dest)
        files += 1
    counts: dict[str, int] = {"files": files}
    with tconn:
        tcols = set(_cols(tconn, "papers"))
        cols = [c for c in row if c in tcols]
        tconn.execute(f"INSERT INTO papers({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [row[c] for c in cols])
        for table in PAPER_TABLES:
            if table not in s_tables:
                continue
            if table not in _tables(tconn):
                continue
            have = set(_cols(tconn, table))
            cols = [c for c in _cols(sconn, table) if c in have and not (table == "events" and c == "id")]
            rows = [tuple(r) for r in sconn.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE paper = ?", (key,))]
            counts[table] = _insert(tconn, table, cols, rows)
        if "vectors" in s_tables and "nodes" in s_tables:
            have = set(_cols(tconn, "vectors"))
            cols = [c for c in _cols(sconn, "vectors") if c in have]
            rows = [tuple(r) for r in sconn.execute(
                f"SELECT {', '.join('v.' + c for c in cols)} FROM vectors v JOIN nodes n ON n.node_id = v.node WHERE n.paper = ? ORDER BY v.rowid", (key,))]
            counts["vectors"] = _insert(tconn, "vectors", cols, rows)
        tconn.execute("INSERT INTO events(paper, at, stage, detail) VALUES (?, ?, ?, ?)", (key, now, "migrated", f"moved into the shared store from {lib.id}"))
    return counts


def _move_labels(sconn: sqlite3.Connection, s_tables: set[str], tconn: sqlite3.Connection, key: str, tkey: str) -> int:
    """The labels a person (or the model) gave a paper the shared store held already, moved to its
    key there; where both have a label for the same finding and method, the shared store's stays."""

    def move(node_id: str | None) -> str | None:
        return tkey + node_id[len(key):] if node_id and tkey != key and node_id.startswith(key + "#") else node_id

    n = 0
    with tconn:
        for table in ("link_labels", "model_labels"):
            if table not in s_tables:
                continue
            for r in sconn.execute(f"SELECT finding, finding_text, method, method_heading, paragraph, paragraph_text, verdict, by, at FROM {table} WHERE paper = ?", (key,)):
                n += tconn.execute(
                    f"INSERT OR IGNORE INTO {table}(paper, finding, finding_text, method, method_heading, paragraph, paragraph_text, verdict, by, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (tkey, move(r["finding"]), r["finding_text"], move(r["method"]) or "", r["method_heading"], move(r["paragraph"]), r["paragraph_text"], r["verdict"], r["by"], r["at"])).rowcount
    return n


def _copy_candidates(lib: Library, sconn: sqlite3.Connection, s_tables: set[str], tconn: sqlite3.Connection, keys: dict[str, str], now: str) -> dict[str, int]:
    """A library's candidates into the shared store's, once each by identifier and with their status
    as it was; the project keeps them all. Then the round's rows, every candidate id and paper key
    moved to the shared store's."""
    out = {"candidates": 0, "candidates_new": 0, "cites": 0}
    as_here: dict[str, str] = dict(keys)
    if "candidates" in s_tables:
        tcols = set(_cols(tconn, "candidates"))
        rows = sconn.execute("SELECT * FROM candidates ORDER BY cand_id").fetchall()
        ids: list[int] = []
        with tconn:
            for r in rows:
                d = dict(r)
                hit = {k: d.get(k) for k in acquire._FIELDS}
                hit["pub_types"] = [p.strip() for p in str(d.get("pub_types") or "").split(";") if p.strip()]
                cid, new = acquire.upsert_candidate(tconn, hit, query=d.get("query"), now=d.get("found_at") or now,
                                                    status=d.get("status") or "found", round=int(d.get("round") or 1))
                ids.append(cid)
                as_here[f"cand:{d['cand_id']}"] = f"cand:{cid}"
                out["candidates"] += 1
                if new:
                    out["candidates_new"] += 1
                    carry = {c: d.get(c) for c in ("status", "paper_key", "file", "error", "found_at", "updated_at", "openalex", "oa_url") if c in tcols and c in d}
                    if carry.get("paper_key"):
                        carry["paper_key"] = keys.get(str(carry["paper_key"]), carry["paper_key"])
                    if carry:
                        tconn.execute(f"UPDATE candidates SET {', '.join(f'{c} = ?' for c in carry)} WHERE cand_id = ?", (*carry.values(), cid))
        members.add_candidates(tconn, lib, ids, now)
    with tconn:
        if "cites" in s_tables:
            for citing, cited, origin, ref_no in sconn.execute("SELECT citing, cited, origin, ref_no FROM cites"):
                out["cites"] += tconn.execute("INSERT OR IGNORE INTO cites VALUES (?, ?, ?, ?)",
                                              (as_here.get(citing, citing), as_here.get(cited, cited), origin, ref_no)).rowcount
        if "harvests" in s_tables:
            for paper, kind, at, found in sconn.execute("SELECT paper, kind, at, found FROM harvests WHERE kind != 'local'"):
                tconn.execute("INSERT OR IGNORE INTO harvests VALUES (?, ?, ?, ?)", (keys.get(paper, paper), kind, at, found))
    return out


def migrate_library(lib: Library, tconn: sqlite3.Connection, now: str, on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """One old library into the shared store (open on `tconn`); its manifest marked moved. The
    project is the same project, read from the shared store from now on."""
    say = on_progress or (lambda e: None)
    target = Library(root=lib.root, id=lib.id, manifest=lib.manifest, store_dir=shared_dir(lib.root))
    out: dict[str, Any] = {"id": lib.id, "papers": 0, "new": 0, "held": 0, "files": 0, "vectors": 0, "labels_moved": 0,
                           "candidates": 0, "candidates_new": 0, "cites": 0, "skipped": []}
    keys: dict[str, str] = {}
    if lib.store_path.exists():
        sconn = _read_only(lib.store_path)
        try:
            s_tables = _tables(sconn)
            rows = [dict(r) for r in sconn.execute("SELECT * FROM papers ORDER BY added_at, key")] if "papers" in s_tables else []
            for i, row in enumerate(rows):
                say({"event": "progress", "op": "migrate", "lib": lib.id, "done": i, "total": len(rows), "label": f"Moving {lib.id}: paper {i + 1} of {len(rows)}"})
                out["papers"] += 1
                key = str(row["key"])
                tkey = _held(tconn, row)
                if tkey is not None:
                    out["held"] += 1
                    out["labels_moved"] += _move_labels(sconn, s_tables, tconn, key, tkey)
                else:
                    try:
                        got = _copy_paper(lib, sconn, s_tables, tconn, row, now)
                    except sqlite3.IntegrityError as e:  # an identifier the shared store holds under another paper: never two rows for one
                        out["skipped"].append({"paper": key, "reason": str(e)})
                        continue
                    tkey = key
                    out["new"] += 1
                    out["files"] += got["files"]
                    out["vectors"] += got.get("vectors", 0)
                keys[key] = tkey
                members.add(tconn, target, [tkey], row.get("added_at") or now)
            out.update({k: v for k, v in _copy_candidates(target, sconn, s_tables, tconn, keys, now).items()})
        finally:
            sconn.close()
    acquire.reconcile(tconn)
    graph.sync(tconn)

    def mark(m: dict[str, Any]) -> None:
        m["store"] = "shared"
        m["migratedAt"] = now
        m["migratedFrom"] = str(lib.store_path)

    update_manifest(lib, mark)
    return out


def migrate(root: Path, *, dry_run: bool = False, only: list[str] | None = None,
            on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Every library of the old shape (or those of `only`) moved into the shared store, after a
    backup: `{dry_run, backup, moved: [per library], plan}`. With `dry_run`, the plan alone, and
    nothing written — not even the backup."""
    root = Path(root)
    the_plan = plan(root, only)
    libs = legacy(root, only)
    if dry_run or not libs:
        return {"dry_run": dry_run, "backup": None, "moved": [], "plan": the_plan}
    now = now_iso()
    folder = backup(root, libs, now)
    tconn = open_store(shared_dir(root) / "store.sqlite")
    acquire.ensure_schema(tconn)
    graph.ensure_schema(tconn)
    from . import figures, retrieve

    figures.ensure_schema(tconn)
    retrieve.ensure_schema(tconn)
    moved = []
    try:
        for lib in libs:
            moved.append(migrate_library(lib, tconn, now, on_progress))
    finally:
        tconn.close()
    return {"dry_run": False, "backup": str(folder), "moved": moved, "plan": the_plan}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m litrag_parser.migrate", description="Move libraries of the old shape into the shared store, after a backup.")
    ap.add_argument("--root", type=Path, default=None, help="where the libraries live (LITRAG_ROOT)")
    ap.add_argument("--lib", action="append", default=None, help="only this library (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="say what would move, write nothing")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    root = a.root or library_root()
    out = migrate(root, dry_run=a.dry_run, only=a.lib)
    if a.json:
        print(json.dumps(out, indent=2))
        return 0
    for e in out["plan"]["libraries"]:
        print(f"{e['id']}: {e['papers']} papers ({e['new']} new to the shared store, {e['held']} held there already), {e['candidates']} candidates, {e['vectors']} vectors, {e['files']} files ({e['bytes'] / 1e6:.1f} MB)")
    if not out["plan"]["libraries"]:
        print("nothing to move: every library is in the shared store")
    elif a.dry_run:
        print("dry run: nothing written")
    else:
        print(f"moved; backup in {out['backup']}; the old stores are left where they were")
    return 0


if __name__ == "__main__":
    sys.exit(main())
