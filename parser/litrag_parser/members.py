"""Which projects hold which papers, in the shared store.

A paper is stored once, in `<root>/_shared`, under the key its identifier gives it (DOI, then
PMID, then the file's hash: store.file_paper), whichever project it arrived through. A project is
the list of the papers it holds — `project_papers` rows — so the same paper can be in several
projects, read once, and a project that lets it go takes nothing from the others. A candidate (a
work a search or a citation round found, not held) is kept once the same way, and a project's are
the ones its own searches found (`project_candidates`) and the ones its papers cite or are cited by.

Every read of "the library" the worker answers for a project goes through `scope`: the keys of its
papers, or None where the store is the project's alone — a library of the old shape, its own store
in its own folder (migrate.py moves it), or the scope of all projects together (library.ALL).
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from .library import Library, now_iso


def scope(conn: sqlite3.Connection, lib: Library) -> list[str] | None:
    """The keys of the papers `lib` holds, in the order they were added; None when every paper of
    its store is its own."""
    if not lib.scoped:
        return None
    return [r[0] for r in conn.execute(
        "SELECT pp.paper FROM project_papers pp JOIN papers p ON p.key = pp.paper WHERE pp.project = ? ORDER BY p.added_at, p.key", (lib.id,))]


def scope_of(conn: sqlite3.Connection, libs: Iterable[Library]) -> list[str] | None:
    """The papers several projects hold between them, each once: None when one of them is the
    scope of every paper. Projects of the old shape keep their own stores and cannot be read with
    others: a ValueError says which, so the caller can say to move them first."""
    libs = list(libs)
    own = [l.id for l in libs if not l.shared]
    if own and len(libs) > 1:
        raise ValueError(f"these projects keep their own store and are read one at a time until moved into the shared store: {', '.join(own)}")
    keys: dict[str, None] = {}
    for lib in libs:
        got = scope(conn, lib)
        if got is None:
            return None
        keys.update(dict.fromkeys(got))
    return list(keys)


def holds(conn: sqlite3.Connection, lib: Library, key: str) -> bool:
    if not lib.scoped:
        return conn.execute("SELECT 1 FROM papers WHERE key = ?", (key,)).fetchone() is not None
    return conn.execute("SELECT 1 FROM project_papers WHERE project = ? AND paper = ?", (lib.id, key)).fetchone() is not None


def add(conn: sqlite3.Connection, lib: Library, keys: Iterable[str], now: str | None = None) -> int:
    """`lib` holds these papers now; returns how many it did not before. Nothing for a library
    whose store is its own."""
    if not lib.scoped:
        return 0
    at = now or now_iso()
    n = 0
    for key in keys:
        n += conn.execute("INSERT OR IGNORE INTO project_papers(project, paper, added_at) VALUES (?, ?, ?)", (lib.id, key, at)).rowcount
    conn.commit()
    return n


def remove(conn: sqlite3.Connection, lib: Library, keys: Iterable[str]) -> int:
    """`lib` lets these papers go. The papers stay in the shared store, with every other project
    that holds them, and come back as they were read if the project takes them again."""
    if not lib.scoped:
        raise ValueError(f"{lib.id} keeps its own store: move it into the shared store first")
    n = 0
    for key in keys:
        n += conn.execute("DELETE FROM project_papers WHERE project = ? AND paper = ?", (lib.id, key)).rowcount
    conn.commit()
    return n


def projects_of(conn: sqlite3.Connection, key: str) -> list[str]:
    return [r[0] for r in conn.execute("SELECT project FROM project_papers WHERE paper = ? ORDER BY project", (key,))]


def drop_project(conn: sqlite3.Connection, project: str) -> dict[str, int]:
    """A project's rows of membership gone, its papers and candidates left in the store."""
    papers = conn.execute("DELETE FROM project_papers WHERE project = ?", (project,)).rowcount
    cands = conn.execute("DELETE FROM project_candidates WHERE project = ?", (project,)).rowcount
    conn.commit()
    return {"papers": papers, "candidates": cands}


def copy_project(conn: sqlite3.Connection, source: str, target: str, now: str | None = None) -> dict[str, int]:
    """Every paper and candidate `source` holds, held by `target` too (a merge of projects that
    share a store: nothing to file, nothing to read again)."""
    at = now or now_iso()
    papers = conn.execute("INSERT OR IGNORE INTO project_papers(project, paper, added_at) SELECT ?, paper, ? FROM project_papers WHERE project = ?", (target, at, source)).rowcount
    cands = conn.execute("INSERT OR IGNORE INTO project_candidates(project, cand_id, added_at) SELECT ?, cand_id, ? FROM project_candidates WHERE project = ?", (target, at, source)).rowcount
    conn.commit()
    return {"papers": papers, "candidates": cands}


def add_candidates(conn: sqlite3.Connection, lib: Library, cand_ids: Iterable[int], now: str | None = None) -> None:
    if not lib.scoped:
        return
    at = now or now_iso()
    conn.executemany("INSERT OR IGNORE INTO project_candidates(project, cand_id, added_at) VALUES (?, ?, ?)", [(lib.id, int(c), at) for c in cand_ids])
    conn.commit()


def candidate_ids(conn: sqlite3.Connection, lib: Library, keys: list[str] | None = None) -> set[int] | None:
    """The candidates a project has: those its searches found, those a paper it holds cites or is
    cited by (`cites`, written by a citation round), and those it holds as papers now. None where
    every candidate of the store is the library's own."""
    if not lib.scoped:
        return None
    held = keys if keys is not None else (scope(conn, lib) or [])
    ids = {r[0] for r in conn.execute("SELECT cand_id FROM project_candidates WHERE project = ?", (lib.id,))}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "candidates" not in tables:
        return ids
    if held:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS scope_keys (key TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM scope_keys")
        conn.executemany("INSERT OR IGNORE INTO scope_keys VALUES (?)", [(k,) for k in held])
        if "cites" in tables:
            for (work,) in conn.execute(
                    "SELECT cited FROM cites WHERE citing IN (SELECT key FROM scope_keys) AND cited LIKE 'cand:%' "
                    "UNION SELECT citing FROM cites WHERE cited IN (SELECT key FROM scope_keys) AND citing LIKE 'cand:%'"):
                try:
                    ids.add(int(str(work)[5:]))
                except ValueError:
                    pass
        ids |= {r[0] for r in conn.execute("SELECT cand_id FROM candidates WHERE paper_key IN (SELECT key FROM scope_keys)")}
        conn.execute("DELETE FROM scope_keys")
    return ids


def only(rows: list[dict[str, Any]], keys: list[str] | None, field: str = "key") -> list[dict[str, Any]]:
    """The rows whose `field` is one of `keys`; all of them when `keys` is None."""
    if keys is None:
        return rows
    want = set(keys)
    return [r for r in rows if r.get(field) in want]
