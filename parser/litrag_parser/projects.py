"""A project is a library: one project's papers, its searches, and the research context it serves.

`summary` is what the window shows of a project, read without writing anything; `describe`
names it and writes the description — the research context Karim writes for it — into its
manifest; `merge` makes one project from several, filing each paper once (DOI, then PMID, then
hash, so the same paper held by two libraries files once — the first one read wins) and bringing
its raw Docling document, its record and the model's stored answers with it, so the worker's
`rebuild` derives its rows again without running Docling. Sources are only ever read. There is
no delete here: nothing in this module destroys anything.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from . import acquire
from .library import Library, create_library, list_libraries, now_iso, open_library, queries_of, safe_key, update_manifest
from .store import file_paper, log_event, open_store, sha256_of


def _read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}


def _manifest(lib: Library) -> dict[str, Any]:
    try:
        return json.loads(lib.manifest_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(lib.manifest)


def _write_manifest(lib: Library, manifest: dict[str, Any]) -> None:
    update_manifest(lib, lambda m: (m.clear(), m.update(manifest)))


def summary(lib: Library) -> dict[str, Any]:
    """A project at a glance: `{id, name, description, createdAt, projectId, dir, queries,
    counts: {papers, parsed, failed, pdf, xml, by_type, nodes, candidates, vectors}}`.
    Read-only; a library with no store yet counts nothing."""
    m = _manifest(lib)
    counts: dict[str, Any] = {"papers": 0, "parsed": 0, "failed": 0, "pdf": 0, "xml": 0, "by_type": {}, "nodes": 0, "candidates": {}, "vectors": 0}
    if lib.store_path.exists():
        conn = _read_only(lib.store_path)
        try:
            have = _tables(conn)
            if "papers" in have:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
                counts["papers"] = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
                counts["parsed"] = conn.execute("SELECT COUNT(*) FROM papers WHERE status = 'parsed'").fetchone()[0]
                counts["failed"] = conn.execute("SELECT COUNT(*) FROM papers WHERE status = 'failed'").fetchone()[0]
                counts["pdf"] = conn.execute("SELECT COUNT(*) FROM papers WHERE format = 'pdf'").fetchone()[0]
                counts["xml"] = conn.execute("SELECT COUNT(*) FROM papers WHERE format = 'jats'").fetchone()[0]
                if "type" in cols:
                    counts["by_type"] = {(r[0] or "unknown"): r[1] for r in conn.execute("SELECT type, COUNT(*) FROM papers GROUP BY type ORDER BY type")}
            if "nodes" in have:
                counts["nodes"] = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            if "candidates" in have:
                counts["candidates"] = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM candidates GROUP BY status ORDER BY status")}
            if "vectors" in have:
                counts["vectors"] = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        except sqlite3.Error as e:
            counts["error"] = str(e)
        finally:
            conn.close()
    return {
        "id": lib.id,
        "name": m.get("name", lib.id),
        "description": m.get("description"),
        "createdAt": m.get("createdAt"),
        "projectId": m.get("projectId"),
        "dir": str(lib.dir),
        "mergedFrom": m.get("mergedFrom", []),
        "queries": queries_of(m),  # a library from before the studio kept each search as its bare query string
        "counts": counts,
    }


def describe(lib: Library, name: str | None = None, description: str | None = None) -> dict[str, Any]:
    """Rename a project, or write its description (its research context). The id — its folder —
    never changes. Returns the summary."""
    if name is not None:
        if not name.strip():
            raise ValueError("a project's name cannot be empty")
        for other in list_libraries(lib.root):
            if other.id != lib.id and name.strip().lower() in (other.id, str(other.manifest.get("name", "")).lower()):
                raise ValueError(f"another project is already called {name.strip()!r}")

    def change(m: dict[str, Any]) -> None:
        if name is not None:
            m["name"] = name.strip()
        if description is not None:
            m["description"] = description.strip()
        m["updatedAt"] = now_iso()

    update_manifest(lib, change)
    return summary(lib)


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)


_RECORD = ("pub_types", "authors", "journal", "year", "type", "type_source", "type_detail", "subtype")


def merge(root: Path, source_ids: list[str], target_name: str, into: str | None = None) -> dict[str, Any]:
    """Make one library of several: `{target, filed, duplicates, keys_to_rebuild, keys_to_parse,
    skipped, candidates_merged, queries_merged}`.

    Each source's papers, in the order they were added, are filed in the target by
    `store.file_paper` — so a DOI two sources hold files once, the first one read wins, and the
    second counts as a duplicate. A paper new to the target brings its file, its raw Docling
    document (and then its status is `parsed`, so `rebuild` will read it), its record, and its
    `outlines` and `judgments` rows (so a rebuild replays the model's answers rather than asking
    again). Candidates merge without duplicates, keeping their status; the manifests' `queries`
    are united. The sources are opened read-only.

    `keys_to_rebuild` is every paper of this merge the target holds as parsed with its raw
    document and no rows yet: the worker runs `rebuild` on them. `keys_to_parse` has a file and
    no raw document: the worker reads it again. Merging twice files nothing new."""
    root = Path(root)
    sources: list[Library] = []
    for sid in source_ids:
        lib = open_library(root, sid)
        if lib is None:
            raise ValueError(f"no such library: {sid}")
        if all(lib.id != s.id for s in sources):
            sources.append(lib)
    if not sources:
        raise ValueError("a merge needs at least one source library")
    if into:
        target = open_library(root, into)
        if target is None:
            raise ValueError(f"no such library to merge into: {into}")
    else:
        target = create_library(root, target_name)  # raises when one of that name exists: merge `into` it instead
    if any(s.id == target.id for s in sources):
        raise ValueError(f"a library cannot be merged into itself: {target.id}")
    target.ensure_dirs()

    tconn = open_store(target.store_path)
    acquire.ensure_schema(tconn)
    t_tables = _tables(tconn)
    filed = duplicates = candidates_merged = 0
    touched: list[str] = []
    skipped: list[dict[str, str]] = []
    queries: list[dict[str, Any]] = []
    now = now_iso()
    try:
        for src in sources:
            queries.extend(_manifest(src).get("queries") or [])
            if not src.store_path.exists():
                continue
            sconn = _read_only(src.store_path)
            try:
                s_tables = _tables(sconn)
                if "papers" in s_tables:
                    for row in sconn.execute("SELECT * FROM papers ORDER BY added_at, key").fetchall():
                        outcome = _merge_paper(src, sconn, s_tables, target, tconn, t_tables, dict(row), now)
                        if outcome["kind"] == "filed":
                            filed += 1
                        elif outcome["kind"] == "duplicate":
                            duplicates += 1
                        else:
                            skipped.append({"library": src.id, "paper": row["key"], "reason": outcome["reason"]})
                        if outcome.get("key"):
                            touched.append(outcome["key"])
                if "candidates" in s_tables:
                    candidates_merged += _merge_candidates(sconn, tconn, now)
            finally:
                sconn.close()
        acquire.reconcile(tconn)

        keys_to_rebuild: list[str] = []
        keys_to_parse: list[str] = []
        for key in dict.fromkeys(touched):
            r = tconn.execute("SELECT status, file, (SELECT COUNT(*) FROM nodes n WHERE n.paper = papers.key) AS n FROM papers WHERE key = ?", (key,)).fetchone()
            if r is None:
                continue
            raw = target.parsed_dir / f"{safe_key(key)}.docling.json"
            if r["status"] == "parsed" and raw.exists():
                if not r["n"]:
                    keys_to_rebuild.append(key)
            elif r["file"] and (target.papers_dir / r["file"]).exists() and r["status"] != "parsed":
                keys_to_parse.append(key)
    finally:
        tconn.close()

    new_queries: list[dict[str, Any]] = []

    def change(m: dict[str, Any]) -> None:
        # a library from before the studio kept its searches as bare strings
        held = queries_of(m)
        have = {(q.get("query"), q.get("at")) for q in held}
        for q in (q if isinstance(q, dict) else {"query": str(q)} for q in queries):
            k = (q.get("query"), q.get("at"))
            if k not in have:
                have.add(k)
                new_queries.append(q)
        m["queries"] = sorted([*held, *new_queries], key=lambda q: str(q.get("at") or ""))
        m["mergedFrom"] = sorted(set(m.get("mergedFrom") or []) | {s.id for s in sources})

    update_manifest(target, change)

    return {
        "target": target.to_dict(),
        "sources": [s.id for s in sources],
        "filed": filed,
        "duplicates": duplicates,
        "skipped": skipped,
        "keys_to_rebuild": keys_to_rebuild,
        "keys_to_parse": keys_to_parse,
        "candidates_merged": candidates_merged,
        "queries_merged": len(new_queries),
    }


def _merge_paper(src: Library, sconn: sqlite3.Connection, s_tables: set[str], target: Library, tconn: sqlite3.Connection,
                 t_tables: set[str], row: dict[str, Any], now: str) -> dict[str, Any]:
    key = row["key"]
    file = src.papers_dir / row["file"] if row.get("file") else None
    if file is not None and not file.exists():
        file = None
    raw = src.parsed_dir / f"{safe_key(key)}.docling.json"
    sha = row.get("sha256") or (sha256_of(file) if file else None)
    if not sha:
        return {"kind": "skipped", "reason": "no file and no hash to file it by"}
    doi = row.get("doi")
    if doi:
        # the store matches a DOI as written; the same DOI printed in another case is the same paper
        same = tconn.execute("SELECT doi FROM papers WHERE lower(doi) = lower(?)", (doi,)).fetchone()
        doi = same["doi"] if same else doi
    result = file_paper(tconn, title=row.get("title") or key, file="", sha256=sha, fmt=row.get("format") or "", doi=doi,
                        pmid=row.get("pmid"), pmcid=row.get("pmcid"), now=row.get("added_at") or now)
    tkey = result.key
    have = tconn.execute("SELECT file, status FROM papers WHERE key = ?", (tkey,)).fetchone()
    if result.existed and have is not None and (have["file"] or have["status"] == "parsed"):
        _merge_labels(sconn, s_tables, tconn, t_tables, key, tkey)
        tconn.commit()
        return {"kind": "duplicate", "key": tkey}

    dest_name = None
    if file is not None:
        dest = target.papers_dir / f"{safe_key(tkey)}{file.suffix.lower()}"
        _copy(file, dest)
        dest_name = dest.name
    raw_came = False
    if raw.exists():
        _copy(raw, target.parsed_dir / f"{safe_key(tkey)}.docling.json")
        raw_came = True
    sets = {"title": row.get("title") or tkey, "file": dest_name, "format": row.get("format"), "sha256": sha,
            "pages": row.get("pages"), "has_methods": row.get("has_methods"),
            # parsed only when the source had read it: a paper that failed after its raw document was
            # saved keeps its failure's reason and is read again, never passed off as read
            "status": "parsed" if raw_came and row.get("status") == "parsed" else "queued",
            "error": None if raw_came and row.get("status") == "parsed" else row.get("error")}
    for col in _RECORD:
        if col in row:
            sets[col] = row[col]
    tcols = {r[1] for r in tconn.execute("PRAGMA table_info(papers)")}
    sets = {k: v for k, v in sets.items() if k in tcols}
    tconn.execute(f"UPDATE papers SET {', '.join(f'{k} = ?' for k in sets)} WHERE key = ?", (*sets.values(), tkey))

    if "outlines" in s_tables and "outlines" in t_tables:
        for o in sconn.execute("SELECT signature, model, outline, prompt_tokens, answer_tokens, seconds, at FROM outlines WHERE paper = ?", (key,)):
            tconn.execute("INSERT OR IGNORE INTO outlines(paper, signature, model, outline, prompt_tokens, answer_tokens, seconds, at) VALUES (?,?,?,?,?,?,?,?)", (tkey, *tuple(o)))
    if "judgments" in s_tables and "judgments" in t_tables:
        for j in sconn.execute("SELECT pair, same, model, at FROM judgments WHERE paper = ?", (key,)):
            tconn.execute("INSERT OR IGNORE INTO judgments(paper, pair, same, model, at) VALUES (?,?,?,?,?)", (tkey, *tuple(j)))
    _merge_labels(sconn, s_tables, tconn, t_tables, key, tkey)
    _merge_charts(sconn, s_tables, tconn, key, tkey)
    tconn.commit()
    log_event(tconn, tkey, now, "merged", f"from {src.id} ({key}){'' if raw_came else ', no raw document: to be read again'}")
    return {"kind": "filed", "key": tkey}


def _merge_labels(sconn: sqlite3.Connection, s_tables: set[str], tconn: sqlite3.Connection, t_tables: set[str], key: str, tkey: str) -> None:
    """A person's finding→method labels (truth.py), and the local model's (labeller.py), go with
    the paper, the target's own kept where both have one. Node ids under another key are moved
    to it; where the target reads the paper differently, truth.anchor finds them again by their
    words."""

    def move(node_id: str | None) -> str | None:
        return tkey + node_id[len(key):] if node_id and tkey != key and node_id.startswith(key + "#") else node_id

    for table in ("link_labels", "model_labels"):
        if table not in s_tables or table not in t_tables:
            continue
        for r in sconn.execute(f"SELECT finding, finding_text, method, method_heading, paragraph, paragraph_text, verdict, by, at FROM {table} WHERE paper = ?", (key,)):
            tconn.execute(
                f"INSERT OR IGNORE INTO {table}(paper, finding, finding_text, method, method_heading, paragraph, paragraph_text, verdict, by, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tkey, move(r["finding"]), r["finding_text"], move(r["method"]) or "", r["method_heading"], move(r["paragraph"]), r["paragraph_text"], r["verdict"], r["by"], r["at"]),
            )


def _merge_charts(sconn: sqlite3.Connection, s_tables: set[str], tconn: sqlite3.Connection, key: str, tkey: str) -> None:
    """The numbers read from a filed paper's figures (figures.py) go with it, figure ids moved to
    the key the target holds it by; a rebuild there finds each figure again by its place."""
    if "charts" not in s_tables:
        return
    from .figures import ensure_schema

    ensure_schema(tconn)

    def move(node_id: str) -> str:
        return tkey + node_id[len(key):] if tkey != key and node_id.startswith(key + "#") else node_id

    for table in ("charts", "chart_values"):
        if table not in s_tables:
            continue
        cols = [r[1] for r in sconn.execute(f"PRAGMA table_info({table})")]
        for r in sconn.execute(f"SELECT * FROM {table} WHERE paper = ?", (key,)):
            row = dict(zip(cols, r))
            row["paper"], row["figure"] = tkey, move(row["figure"])
            tconn.execute(f"INSERT OR IGNORE INTO {table}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [row[c] for c in cols])
    if "figure_reads" in s_tables:
        for r in sconn.execute("SELECT reader, figures, plots, read, values_, seconds, at FROM figure_reads WHERE paper = ?", (key,)):
            tconn.execute("INSERT OR IGNORE INTO figure_reads(paper, reader, figures, plots, read, values_, seconds, at) VALUES (?,?,?,?,?,?,?,?)", (tkey, *tuple(r)))


def _merge_candidates(sconn: sqlite3.Connection, tconn: sqlite3.Connection, now: str) -> int:
    """A source's candidates into the target's, once each. A fetch's file stayed in the source's
    inbox, so a fetched candidate is found again here, to be fetched again if wanted; an ingested
    one is `ingested` here only when `reconcile` finds its paper in the target."""
    from . import graph

    added = 0
    rows = sconn.execute("SELECT * FROM candidates ORDER BY cand_id").fetchall()
    graph.ensure_schema(tconn)
    as_here: dict[str, str] = {}
    with tconn:
        for r in rows:
            d = dict(r)
            hit = {k: d.get(k) for k in acquire._FIELDS}
            hit["pub_types"] = [p.strip() for p in str(d.get("pub_types") or "").split(";") if p.strip()]
            status = d.get("status") or "found"
            if status in ("fetching", "fetched", "ingested"):
                status = "found"  # the file is not carried, and whether it is held here is reconcile's to say
            cid, new = acquire.upsert_candidate(tconn, hit, query=d.get("query"), now=d.get("found_at") or now, status=status, round=int(d.get("round") or 1))
            as_here[f"cand:{d['cand_id']}"] = f"cand:{cid}"
            if new:
                added += 1
                tconn.execute("UPDATE candidates SET error = ?, updated_at = ? WHERE cand_id = ?", (d.get("error"), now, cid))
        # the citations a round found, between the same works here (a paper's key is the same in every library)
        tables = {t for (t,) in sconn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "cites" in tables:
            for citing, cited, origin, ref_no in sconn.execute("SELECT citing, cited, origin, ref_no FROM cites"):
                tconn.execute("INSERT OR IGNORE INTO cites VALUES (?, ?, ?, ?)", (as_here.get(citing, citing), as_here.get(cited, cited), origin, ref_no))
        if "harvests" in tables:
            for paper, kind, at, found in sconn.execute("SELECT paper, kind, at, found FROM harvests WHERE kind != 'local'"):
                tconn.execute("INSERT OR IGNORE INTO harvests VALUES (?, ?, ?, ?)", (paper, kind, at, found))
    return added
