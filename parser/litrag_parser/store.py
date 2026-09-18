"""The store: SQLite, rows first.

Everything the parser learns about a paper is a row a person can SELECT. The
raw Docling document is kept beside the store as JSON and never edited; the
rows are built from it and can be rebuilt from it. Filing is idempotent — a
paper is keyed by DOI, then PMID, then the hash of its file — and parsing a
paper again replaces its rows in one transaction.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .tree import Tree

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
  key TEXT PRIMARY KEY,
  doi TEXT, pmid TEXT, pmcid TEXT,
  title TEXT NOT NULL,
  file TEXT, sha256 TEXT, format TEXT,
  pages INTEGER,
  status TEXT NOT NULL DEFAULT 'queued',
  error TEXT,
  parser TEXT,
  added_at TEXT NOT NULL,
  parsed_at TEXT,
  seconds REAL,
  has_methods INTEGER,
  type TEXT,                        -- research | review | letter | editorial | case-report | protocol | data | correction | other
  type_source TEXT,                 -- record | jats | subject | title | printed | shape | default | meaning | none
  type_detail TEXT,
  subtype TEXT,                     -- rct | systematic-review | case-series | brief-report | … when a label states one (paper_type.py)
  pub_types TEXT,                   -- Europe PMC's publication types, "; "-joined, fetched once at ingest
  authors TEXT,                     -- JSON [{name, affiliations, corresponding}]: the JATS file's word, else the record's (record.py)
  journal TEXT,
  year TEXT,
  confidence REAL,                  -- how far the reading can be trusted, from the reading alone, in (0, 1] (confidence.py)
  confidence_detail TEXT            -- JSON {reasons: [...], penalties: {check: points}}: why it is not 1
);
CREATE UNIQUE INDEX IF NOT EXISTS papers_doi ON papers(doi) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS papers_sha ON papers(sha256) WHERE sha256 IS NOT NULL;

CREATE TABLE IF NOT EXISTS pages (
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  page_no INTEGER NOT NULL,
  width REAL NOT NULL, height REAL NOT NULL,
  PRIMARY KEY(paper, page_no)
);

CREATE TABLE IF NOT EXISTS nodes (
  node_id TEXT PRIMARY KEY,
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  parent TEXT,
  ordinal INTEGER NOT NULL,
  depth INTEGER NOT NULL,
  type TEXT NOT NULL,
  label TEXT NOT NULL,
  level INTEGER,
  role TEXT NOT NULL,
  heading TEXT,
  ancestry TEXT NOT NULL,      -- JSON list of headings, top down
  text TEXT NOT NULL,
  page INTEGER,
  bbox_l REAL, bbox_t REAL, bbox_r REAL, bbox_b REAL,
  self_ref TEXT,
  table_json TEXT,             -- {"rows","cols","cells"} for tables
  canonical TEXT               -- the catalogue's name for a section (headings.py), NULL when it has none
);
CREATE INDEX IF NOT EXISTS nodes_paper ON nodes(paper, ordinal);
CREATE INDEX IF NOT EXISTS nodes_parent ON nodes(parent);
CREATE INDEX IF NOT EXISTS nodes_role ON nodes(paper, role, type);

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(text, content='nodes', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
  INSERT INTO nodes_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;

CREATE TABLE IF NOT EXISTS refs (
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  ref_no INTEGER NOT NULL,          -- 1-based, in the order of the reference list
  node_id TEXT,                     -- the entry's own node
  ref_id TEXT,                      -- the JATS id, when there is one
  text TEXT NOT NULL,
  doi TEXT, pmid TEXT, year TEXT, first_author TEXT, title TEXT,
  PRIMARY KEY(paper, ref_no)
);
CREATE TABLE IF NOT EXISTS citations (
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  node_id TEXT NOT NULL,
  ref_no INTEGER NOT NULL,
  marker TEXT NOT NULL,             -- the text that named the entry: "[6,9,12]", "Lyon, 2020"
  PRIMARY KEY(paper, node_id, ref_no)
);
CREATE INDEX IF NOT EXISTS citations_ref ON citations(paper, ref_no);

CREATE TABLE IF NOT EXISTS judgments (
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  pair TEXT NOT NULL,               -- sha1 of the two blocks' ends (judge.pair_key)
  same INTEGER NOT NULL,            -- 1: one paragraph; 0: two
  model TEXT NOT NULL,
  at TEXT NOT NULL,
  PRIMARY KEY(paper, pair)
);

CREATE TABLE IF NOT EXISTS edges (
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  src TEXT NOT NULL,                -- a node: the finding, the citing paragraph
  dst TEXT NOT NULL,                -- a node: the method subsection, the figure
  kind TEXT NOT NULL,               -- measured_by | cites_figure
  evidence TEXT NOT NULL,           -- pointer | terms | caption | similarity | mention
  detail TEXT,                      -- what made it: "Section 2.3", "compressive modulus", "cosine 0.71 margin 0.09"
  score REAL,
  PRIMARY KEY(paper, src, dst, kind)
);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  paper TEXT NOT NULL,
  at TEXT NOT NULL,
  stage TEXT NOT NULL,
  detail TEXT
);
"""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def open_store(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
    for col in ("type", "type_source", "type_detail", "pub_types", "authors", "journal", "year", "subtype"):
        if col not in have:  # a store from before the paper's type, or its record, was a column
            conn.execute(f"ALTER TABLE papers ADD COLUMN {col} TEXT")
    if "confidence" not in have:  # a store from before a reading was scored
        conn.execute("ALTER TABLE papers ADD COLUMN confidence REAL")
        conn.execute("ALTER TABLE papers ADD COLUMN confidence_detail TEXT")
    if "canonical" not in {r[1] for r in conn.execute("PRAGMA table_info(nodes)")}:
        conn.execute("ALTER TABLE nodes ADD COLUMN canonical TEXT")  # a store from before headings had a canonical name
    conn.commit()
    return conn


@dataclass
class Filed:
    key: str
    existed: bool


def file_paper(conn: sqlite3.Connection, *, title: str, file: str, sha256: str, fmt: str, doi: str | None, pmid: str | None, pmcid: str | None, now: str) -> Filed:
    """File a paper once: by DOI, then PMID, then hash. Seeing it again fills gaps."""
    row = None
    if doi:
        row = conn.execute("SELECT key FROM papers WHERE doi = ?", (doi,)).fetchone()
    if row is None and pmid:
        row = conn.execute("SELECT key FROM papers WHERE pmid = ?", (pmid,)).fetchone()
    sha_row = conn.execute("SELECT key FROM papers WHERE sha256 = ?", (sha256,)).fetchone()
    if row is None:
        row = sha_row
    elif sha_row is not None and sha_row["key"] != row["key"]:
        # The same bytes were filed earlier under a hash, before their DOI was known:
        # that row was a stub of this paper. It goes, with its rows; the identified paper stays.
        conn.execute("DELETE FROM papers WHERE key = ?", (sha_row["key"],))
    if row is not None:
        key = row["key"]
        # Seeing a paper again fills gaps and never moves it backwards: an identifier it
        # lacked, a file if it had none. The caller decides whether to swap a file it has.
        conn.execute(
            "UPDATE papers SET doi = COALESCE(doi, ?), pmid = COALESCE(pmid, ?), pmcid = COALESCE(pmcid, ?), file = COALESCE(file, ?), sha256 = COALESCE(sha256, ?), format = COALESCE(format, ?) WHERE key = ?",
            (doi, pmid, pmcid, file or None, sha256, fmt, key),
        )
        return Filed(key=key, existed=True)
    key = f"doi:{doi}" if doi else f"pmid:{pmid}" if pmid else f"pmcid:{pmcid}" if pmcid else f"sha:{sha256[:16]}"
    conn.execute(
        "INSERT INTO papers(key, doi, pmid, pmcid, title, file, sha256, format, status, added_at) VALUES (?,?,?,?,?,?,?,?, 'queued', ?)",
        (key, doi, pmid, pmcid, title, file, sha256, fmt, now),
    )
    return Filed(key=key, existed=False)


def set_status(conn: sqlite3.Connection, key: str, status: str, *, error: str | None = None) -> None:
    conn.execute("UPDATE papers SET status = ?, error = ? WHERE key = ?", (status, error, key))
    conn.commit()


def log_event(conn: sqlite3.Connection, key: str, at: str, stage: str, detail: str | None = None) -> None:
    conn.execute("INSERT INTO events(paper, at, stage, detail) VALUES (?,?,?,?)", (key, at, stage, detail))
    conn.commit()


def save_tree(conn: sqlite3.Connection, key: str, tree: Tree, *, parser: str, parsed_at: str, seconds: float) -> int:
    """Replace a paper's rows with a tree, in one transaction. Returns the node count."""
    with conn:
        conn.execute("DELETE FROM nodes WHERE paper = ?", (key,))
        conn.execute("DELETE FROM pages WHERE paper = ?", (key,))
        conn.executemany("INSERT INTO pages(paper, page_no, width, height) VALUES (?,?,?,?)", [(key, p.page_no, p.width, p.height) for p in tree.pages])
        rows = []
        for n in tree.walk():
            b = n.bbox or [None, None, None, None]
            rows.append((
                n.node_id, key, n.parent, n.ordinal, n.depth, n.type, n.label, n.level, n.role, n.heading,
                json.dumps(n.ancestry, ensure_ascii=False), n.text, n.page, b[0], b[1], b[2], b[3], n.self_ref,
                json.dumps(n.table, ensure_ascii=False) if n.table else None,
                n.canonical,
            ))
        conn.executemany(
            "INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, level, role, heading, ancestry, text, page, bbox_l, bbox_t, bbox_r, bbox_b, self_ref, table_json, canonical) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.execute(
            "UPDATE papers SET title = ?, pages = ?, status = 'parsed', error = NULL, parser = ?, parsed_at = ?, seconds = ?, has_methods = ? WHERE key = ?",
            (tree.title or key, len(tree.pages), parser, parsed_at, seconds, 1 if tree.has_methods else 0, key),
        )
    return len(rows)


def save_refs(conn: sqlite3.Connection, key: str, refs: Iterable[Any], cites: Iterable[Any]) -> dict[str, int]:
    """Replace a paper's reference entries and citation links, in one transaction."""
    refs, cites = list(refs), list(cites)
    with conn:
        conn.execute("DELETE FROM refs WHERE paper = ?", (key,))
        conn.execute("DELETE FROM citations WHERE paper = ?", (key,))
        conn.executemany(
            "INSERT INTO refs(paper, ref_no, node_id, ref_id, text, doi, pmid, year, first_author, title) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(key, r.ref_no, r.node_id, r.ref_id, r.text, r.doi, r.pmid, r.year, r.first_author, r.title) for r in refs],
        )
        conn.executemany("INSERT OR IGNORE INTO citations(paper, node_id, ref_no, marker) VALUES (?,?,?,?)", [(key, c.node_id, c.ref_no, c.marker) for c in cites])
    return {"refs": len(refs), "citations": len(cites)}


def set_type(conn: sqlite3.Connection, key: str, kind: str, source: str, detail: str, subtype: str | None = None) -> None:
    with conn:
        conn.execute("UPDATE papers SET type = ?, type_source = ?, type_detail = ?, subtype = ? WHERE key = ?", (kind, source, detail, subtype, key))


def set_confidence(conn: sqlite3.Connection, key: str, confidence: float, reasons: list[str], penalties: dict[str, float]) -> None:
    """How far this reading can be trusted, and why not further (confidence.py)."""
    with conn:
        conn.execute("UPDATE papers SET confidence = ?, confidence_detail = ? WHERE key = ?", (confidence, json.dumps({"reasons": reasons, "penalties": penalties}, ensure_ascii=False), key))


def set_record(conn: sqlite3.Connection, key: str, *, pub_types: list[str] | None = None, authors: list[dict[str, Any]] | None = None, journal: str | None = None, year: str | None = None, overwrite: bool = False) -> None:
    """What is known about a paper beyond its text: Europe PMC's publication types, its authors
    (`[{name, affiliations, corresponding}]`, stored as JSON), journal and year. Fills what is
    empty; `overwrite` replaces — the JATS file's own word over the record's."""
    sets: list[str] = []
    args: list[Any] = []
    for col, val in (("pub_types", "; ".join(pub_types) if pub_types else None), ("authors", json.dumps(authors, ensure_ascii=False) if authors else None), ("journal", journal or None), ("year", year or None)):
        if val is None:
            continue
        sets.append(f"{col} = ?" if overwrite else f"{col} = COALESCE({col}, ?)")
        args.append(val)
    if sets:
        with conn:
            conn.execute(f"UPDATE papers SET {', '.join(sets)} WHERE key = ?", (*args, key))


def save_edges(conn: sqlite3.Connection, key: str, edges: Iterable[Any]) -> int:
    """Replace a paper's edges (edges.py), in one transaction."""
    rows = [(key, e.src, e.dst, e.kind, e.evidence, e.detail, e.score) for e in edges]
    with conn:
        conn.execute("DELETE FROM edges WHERE paper = ?", (key,))
        conn.executemany("INSERT OR IGNORE INTO edges(paper, src, dst, kind, evidence, detail, score) VALUES (?,?,?,?,?,?,?)", rows)
    return int(conn.execute("SELECT COUNT(*) FROM edges WHERE paper = ?", (key,)).fetchone()[0])


def edges_of(conn: sqlite3.Connection, node_id: str) -> dict[str, list[dict[str, Any]]]:
    """A node's edges both ways, each with the other node's role, place and first words:
    `out` (what this finding was measured by, what it cites), `in` (the findings measured
    here, the paragraphs citing this figure)."""
    q = """SELECT e.kind, e.evidence, e.detail, e.score, n.node_id, n.type, n.role, n.heading, n.ancestry, n.page, substr(n.text, 1, 200) AS text
           FROM edges e JOIN nodes n ON n.node_id = e.{other}
           WHERE e.{this} = ? ORDER BY e.kind, e.score DESC, n.ordinal"""
    out = [dict(r) for r in conn.execute(q.format(other="dst", this="src"), (node_id,))]
    inc = [dict(r) for r in conn.execute(q.format(other="src", this="dst"), (node_id,))]
    for rows in (out, inc):
        for r in rows:
            r["ancestry"] = json.loads(r["ancestry"] or "[]")
    paper = node_id.split("#", 1)[0]
    subs = conn.execute("SELECT COUNT(*) FROM nodes WHERE paper = ? AND role = 'methods' AND type = 'section' AND level = 2", (paper,)).fetchone()[0]
    paras = conn.execute("SELECT COUNT(*) FROM nodes WHERE paper = ? AND role = 'methods' AND type = 'paragraph'", (paper,)).fetchone()[0] if not subs else 0
    return {"out": out, "in": inc, "candidates": int(subs or paras)}


def refs_of(conn: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
    """A paper's reference list, each entry with the nodes that cite it."""
    cited: dict[int, list[str]] = {}
    for r in conn.execute("SELECT ref_no, node_id FROM citations WHERE paper = ? ORDER BY ref_no, node_id", (key,)):
        cited.setdefault(r["ref_no"], []).append(r["node_id"])
    return [{**dict(r), "cited_by": cited.get(r["ref_no"], [])} for r in conn.execute("SELECT ref_no, node_id, ref_id, text, doi, pmid, year, first_author, title FROM refs WHERE paper = ? ORDER BY ref_no", (key,))]


def cites_of(conn: sqlite3.Connection, node_id: str) -> list[dict[str, Any]]:
    """The entries one node cites, with the marker that named each."""
    return [dict(r) for r in conn.execute(
        """SELECT c.ref_no, c.marker, r.node_id, r.text, r.doi, r.pmid, r.year, r.first_author, r.title
           FROM citations c JOIN refs r ON r.paper = c.paper AND r.ref_no = c.ref_no
           WHERE c.node_id = ? ORDER BY c.ref_no""", (node_id,))]


def cited_by(conn: sqlite3.Connection, key: str, ref_no: int) -> list[dict[str, Any]]:
    """The nodes that cite one entry: id, role, where in the paper, and the text."""
    return [dict(r) for r in conn.execute(
        """SELECT n.node_id, n.role, n.ancestry, n.page, substr(n.text, 1, 200) AS text, c.marker
           FROM citations c JOIN nodes n ON n.node_id = c.node_id
           WHERE c.paper = ? AND c.ref_no = ? ORDER BY n.depth, n.ordinal""", (key, ref_no))]


def judgment(conn: sqlite3.Connection, paper: str, pair: str) -> int | None:
    r = conn.execute("SELECT same FROM judgments WHERE paper = ? AND pair = ?", (paper, pair)).fetchone()
    return None if r is None else int(r["same"])


def save_judgment(conn: sqlite3.Connection, paper: str, pair: str, same: bool, model: str, at: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO judgments(paper, pair, same, model, at) VALUES (?,?,?,?,?)", (paper, pair, 1 if same else 0, model, at))


def list_papers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT p.*, (SELECT COUNT(*) FROM nodes n WHERE n.paper = p.key) AS nodes
           FROM papers p ORDER BY p.added_at DESC, p.key"""
    ).fetchall()
    return [dict(r) for r in rows]


def paper_tree(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    """A paper's tree as nested dicts, rebuilt from the rows."""
    paper = conn.execute("SELECT * FROM papers WHERE key = ?", (key,)).fetchone()
    if paper is None:
        return None
    pages = [dict(r) for r in conn.execute("SELECT page_no, width, height FROM pages WHERE paper = ? ORDER BY page_no", (key,))]
    by_id: dict[str, dict[str, Any]] = {}
    root: dict[str, Any] | None = None
    for r in conn.execute("SELECT * FROM nodes WHERE paper = ? ORDER BY depth, ordinal", (key,)):
        d = _node_dict(r)
        by_id[d["node_id"]] = d
        if d["parent"] is None:
            root = d
    for d in by_id.values():
        if d["parent"] is not None and d["parent"] in by_id:
            by_id[d["parent"]]["children"].append(d)
    for d in by_id.values():
        d["children"].sort(key=lambda c: c["ordinal"])
    for r in conn.execute("SELECT node_id, ref_no FROM citations WHERE paper = ? ORDER BY node_id, ref_no", (key,)):
        if r["node_id"] in by_id:
            by_id[r["node_id"]].setdefault("cites", []).append(r["ref_no"])
    for r in conn.execute("SELECT node_id, ref_no FROM refs WHERE paper = ? AND node_id IS NOT NULL", (key,)):
        if r["node_id"] in by_id:
            by_id[r["node_id"]]["ref_no"] = r["ref_no"]
    roles: dict[str, int] = {}
    for r in conn.execute("SELECT role, COUNT(*) c FROM nodes WHERE paper = ? AND parent IS NOT NULL GROUP BY role", (key,)):
        roles[r["role"]] = r["c"]
    return {"paper": dict(paper), "pages": pages, "roles": roles, "root": root}


def _node_dict(r: sqlite3.Row) -> dict[str, Any]:
    bbox = None if r["bbox_l"] is None else [r["bbox_l"], r["bbox_t"], r["bbox_r"], r["bbox_b"]]
    return {
        "node_id": r["node_id"], "parent": r["parent"], "ordinal": r["ordinal"], "depth": r["depth"],
        "type": r["type"], "label": r["label"], "level": r["level"], "role": r["role"], "heading": r["heading"],
        "ancestry": json.loads(r["ancestry"]), "text": r["text"], "page": r["page"], "bbox": bbox,
        "self_ref": r["self_ref"], "table": json.loads(r["table_json"]) if r["table_json"] else None, "canonical": r["canonical"] if "canonical" in r.keys() else None, "children": [],
    }


def node(conn: sqlite3.Connection, node_id: str) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
    return _node_dict(r) if r else None


def siblings(conn: sqlite3.Connection, node_id: str) -> list[dict[str, Any]]:
    r = conn.execute("SELECT parent FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
    if r is None or r["parent"] is None:
        return []
    return [_node_dict(x) for x in conn.execute("SELECT * FROM nodes WHERE parent = ? ORDER BY ordinal", (r["parent"],))]


def section(conn: sqlite3.Connection, key: str, role: str) -> list[dict[str, Any]]:
    """Every node of a paper in one lane, in reading order — `get_section(doi, role)`."""
    return [_node_dict(x) for x in conn.execute("SELECT * FROM nodes WHERE paper = ? AND role = ? AND parent IS NOT NULL ORDER BY depth, ordinal", (key, role))]


def run_select(conn: sqlite3.Connection, sql: str, limit: int = 200) -> dict[str, Any]:
    """One SELECT (or WITH … SELECT), and only that, against a read-only handle."""
    statement = sql.strip().rstrip(";")
    import re

    bare = re.sub(r"'(?:[^']|'')*'", "''", statement)
    if not re.match(r"^(select|with)\b", statement, re.IGNORECASE) or ";" in bare:
        raise ValueError("sql runs one SELECT (or WITH … SELECT); nothing else.")
    cur = conn.execute(f"SELECT * FROM ({statement}) LIMIT {max(1, int(limit))}")
    rows = [dict(r) for r in cur.fetchall()]
    columns = [d[0] for d in cur.description] if cur.description else []
    return {"columns": columns, "rows": rows}
