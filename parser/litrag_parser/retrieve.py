"""Chunkless retrieval: find small units, then hand each one back with its context from the tree.

The `lit` CLI cut papers into chunks and searched those. The tree needs no chunks: its
paragraphs are already the right size to find, and everything a chunk used to smuggle in —
the section it sat under, the paragraph before it — is a row beside it. So a question is
matched against **units** (paragraphs, list items, captions: `units`) through the words
(FTS5 over `nodes_fts`, bm25) and through the meaning (cosine over `vectors`, one float32
vector per unit from the local embedder), the two rankings fused by reciprocal rank as
`src/query.ts` does (K = 60); and then each hit is **hydrated** (`hydrate`): its section,
the paragraphs just before and after it, and — what a chunk could never carry — when the
hit is a finding, the methods that produced it (`edges` kind `measured_by`), the figures
it cites (`cites_figure`, with their captions) and the references it cites (`citations`).
Every link handed back is a row the reader wrote; nothing here invents one.

Vectors are rows too (`vectors`, keyed by node and model): `REFERENCES nodes ON DELETE
CASCADE`, so a paper read again, or rebuilt, loses its vectors with its nodes, and the
next `embed_library` embeds only what has none. The model key names the text recipe as
well as the model (`nomic-embed-text@doc1`): change what `doc_text` builds and the key
changes, so old vectors are never compared against new ones.

Local only: the embedder is Ollama on 127.0.0.1 (`LITRAG_OLLAMA_URL`), the model
`LITRAG_LANES_MODEL` or nomic-embed-text. When it is down, a search is words only and
says so.

    uv run --project parser python -m litrag_parser.retrieve --store PATH --embed
    uv run --project parser python -m litrag_parser.retrieve --store PATH --query "..." [--k 8] [--json]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from array import array
from typing import Any, Callable, Iterable, Protocol

import numpy as np

from .edges import FINDING_LANES, _terms as terms_of
from .meaning import DEFAULT_MODEL, QUERY, Oracle
from .store import cites_of, node as node_row, open_store

from .lineage import described_elsewhere

DOCUMENT = "search_document: "  # nomic's task prefix for what is searched; QUERY is the question's
RECIPE = "doc1"  # names what doc_text builds; part of the model key, so a new recipe never meets old vectors
RRF_K = 60
UNIT_TYPES = ("paragraph", "list_item", "caption")
PARAGRAPH_LIKE = ("paragraph", "list_item")
MIN_WORDS = 5  # words of two letters or more: below it a "paragraph" is an axis label, an e-mail, a date
DOC_CHARS = 6000  # ~1,500 tokens: under the 2,048 a default Ollama context gives nomic-embed-text
ANCESTRY_CHARS = 400
METHOD_CHARS = 1500
CAPTION_CHARS = 800
CITE_CHARS = 300
FINDING_CHARS = 300
METHODS_SHOWN, GENERAL_SHOWN, FINDINGS_SHOWN, ELSEWHERE_SHOWN = 3, 2, 5, 2
#: the methods parts every finding of a paper shares, named by the heading catalogue (headings.py):
#: real, and shown, but in their own place, so the method that measured the finding comes first
GENERAL_METHODS = frozenset({"Statistical analysis", "Materials"})

_WORD = re.compile(r"[^\W\d_]{2,}")


def _enough_words(text: str | None) -> int:
    """1 when a text has `MIN_WORDS` words, stopping at the fifth: counting every word of a
    library's text cost 650 ms a query on 16,000 nodes, stopping early costs a few."""
    for i, _ in enumerate(_WORD.finditer(text or ""), 1):
        if i >= MIN_WORDS:
            return 1
    return 0


# -- the table ----------------------------------------------------------------------------------

VECTORS = """
CREATE TABLE IF NOT EXISTS vectors (
  node TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
  model TEXT NOT NULL,              -- the embedder and the text recipe: "nomic-embed-text@doc1"
  dims INTEGER NOT NULL,
  vec BLOB NOT NULL,                -- float32, little-endian, as the embedder gave it (not normalised)
  PRIMARY KEY (node, model)
);
-- how many times the vectors changed: the search's cached matrix is reloaded when this moves.
-- A count and the highest rowid are not enough, since SQLite hands a deleted rowid out again:
-- the last paper's vectors dropped by a rebuild and embedded again look unchanged by them.
CREATE TABLE IF NOT EXISTS vectors_gen (n INTEGER NOT NULL);
INSERT INTO vectors_gen (n) SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM vectors_gen);
CREATE TRIGGER IF NOT EXISTS vectors_gen_ai AFTER INSERT ON vectors BEGIN UPDATE vectors_gen SET n = n + 1; END;
CREATE TRIGGER IF NOT EXISTS vectors_gen_ad AFTER DELETE ON vectors BEGIN UPDATE vectors_gen SET n = n + 1; END;
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """The `vectors` table, created once. The cascade needs `PRAGMA foreign_keys=ON`, which
    `store.open_store` sets on every connection."""
    conn.executescript(VECTORS)
    conn.commit()


def _has_vectors(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vectors'").fetchone() is not None


def _prepare(conn: sqlite3.Connection) -> None:
    """The one function the unit rule needs that SQL has not: a count of words."""
    conn.create_function("litrag_prose", 1, _enough_words, deterministic=True)


# -- what is retrievable ---------------------------------------------------------------------------

_UNIT_WHERE = f"""
  n.type IN ({", ".join(repr(t) for t in UNIT_TYPES)})
  AND n.parent IS NOT NULL
  AND n.role != 'references'
  AND COALESCE(json_extract(n.ancestry, '$[0]'), '') != 'Front matter'
  AND n.node_id NOT IN (SELECT node_id FROM refs WHERE node_id IS NOT NULL)
  AND litrag_prose(n.text)
"""


def _filters(roles: Iterable[str] | None, paper: str | None) -> tuple[str, list[Any]]:
    sql, args = "", []
    roles = list(roles or [])
    if roles:
        sql += f" AND n.role IN ({', '.join('?' for _ in roles)})"
        args += roles
    if paper:
        sql += " AND n.paper = ?"
        args.append(paper)
    return sql, args


def units(conn: sqlite3.Connection, paper: str | None = None) -> list[dict[str, Any]]:
    """The retrievable nodes, in reading order (rowid: `save_tree` inserts in a pre-order walk): `{node_id, paper, type, role, ancestry, text}`.

    A unit is a paragraph, a list item or a caption (figures and tables are found through
    their captions) that
      - is not in the reference list: not in the `references` lane, and no `refs` row names it;
      - is not front-matter furniture: not a `meta` node (authors, affiliations, dates …, which
        are not unit types at all) and not under the reader's own "Front matter" section;
      - has at least five words of two letters or more.
    The last rule is what the data asked for: on the looped-ligament library the prose nodes
    below it are chart axis labels read as paragraphs ("160000", "10 Mpa p = 0.005"),
    e-mail addresses, ORCID lines and publication dates. Back matter (acknowledgements, data
    availability) stays retrievable — a `roles` filter keeps it out when it is not wanted.
    Tables are not units: their `text` is empty and their cells are a grid, not prose.
    """
    _prepare(conn)
    extra, args = _filters(None, paper)
    rows = conn.execute(
        f"SELECT n.node_id, n.paper, n.type, n.role, n.ancestry, n.text FROM nodes n WHERE {_UNIT_WHERE}{extra} ORDER BY n.rowid",
        args,
    ).fetchall()
    return [{**dict(r), "ancestry": json.loads(r["ancestry"] or "[]")} for r in rows]


def _cut(text: str, limit: int) -> str:
    """At most `limit` characters, cut at a word boundary."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    space = head.rfind(" ")
    return head[: space if space > limit * 0.8 else limit]


def doc_text(node: Any) -> str:
    """What is embedded for a unit (recipe `doc1`): nomic's document prefix, the headings above
    it joined by " > ", a newline, and its text. The ancestry is capped at 400 characters and
    the text at 6,000 (about 1,500 tokens, cut at a word), so the whole stays inside the 2,048
    tokens a default Ollama context gives nomic-embed-text; the longest paragraph on the
    looped-ligament library is 6,318 characters, so the cap bites on a handful at most."""
    ancestry = node["ancestry"]
    if isinstance(ancestry, str):
        ancestry = json.loads(ancestry or "[]")
    path = _cut(" > ".join(a for a in ancestry if a), ANCESTRY_CHARS)
    return f"{DOCUMENT}{path}\n{_cut(node['text'] or '', DOC_CHARS)}"


def query_text(question: str) -> str:
    return f"{QUERY}{question.strip()}"


# -- the embedder -----------------------------------------------------------------------------------


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]] | None: ...


class OllamaEmbedder:
    """The local embedder: Ollama's `/api/embed` on this machine, the request the oracle makes
    (`meaning.Oracle._embed`) with one difference. The oracle, once refused, stays silent for a
    minute, which suits a reader asking thousands of small questions; a library being embedded
    wants the opposite. Measured on the looped-ligament library: one batch in about sixty
    came back `400` with Ollama's runner failing mid-request, and the same batch went through
    when sent again. So an error the server answers with is tried again, twice, after a short
    pause; a server that does not answer at all (refused, timed out) is down at once, so a
    question is not kept waiting."""

    RETRIES = 2
    BATCH = Oracle.BATCH

    def __init__(self, url: str | None = None, model: str | None = None, timeout: float = 120.0):
        self.model = model or os.environ.get("LITRAG_LANES_MODEL") or DEFAULT_MODEL
        self.url = (url or os.environ.get("LITRAG_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
        self.timeout = timeout
        self.error: str | None = None

    def _post(self, texts: list[str]) -> list[list[float]]:
        req = urllib.request.Request(f"{self.url}/api/embed", data=json.dumps({"model": self.model, "input": texts}).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            out = json.loads(r.read().decode("utf-8"))["embeddings"]
        if len(out) != len(texts):
            raise ValueError(f"{len(out)} embeddings for {len(texts)} texts")
        return out

    def ping(self, timeout: float = 1.0) -> bool:
        """Whether Ollama answers and holds this model — `GET /api/tags`, no embedding. Sets
        `error` when it does not."""
        try:
            with urllib.request.urlopen(f"{self.url}/api/tags", timeout=timeout) as r:
                names = {m.get("name", "") for m in json.loads(r.read().decode("utf-8")).get("models", [])}
        except Exception as e:  # noqa: BLE001 - refused, timed out, nonsense: all mean down
            self.error = f"{type(e).__name__}: {e}"[:200]
            return False
        if not any(n == self.model or n.split(":", 1)[0] == self.model for n in names):
            self.error = f"Ollama does not have {self.model} (ollama pull {self.model})"
            return False
        self.error = None
        return True

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        texts = list(texts)
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            part = texts[i : i + self.BATCH]
            for attempt in range(self.RETRIES + 1):
                try:
                    out.extend(self._post(part))
                    break
                except urllib.error.HTTPError as e:  # the server answered: a runner hiccup is worth another try
                    body = e.read()[:160].decode("utf-8", "replace") if hasattr(e, "read") else ""
                    self.error = f"HTTP {e.code}: {body}".strip()
                except (urllib.error.URLError, OSError, ValueError) as e:  # nobody there, or nonsense back: down
                    self.error = f"{type(e).__name__}: {e}"[:200]
                    return None
                if attempt < self.RETRIES:
                    time.sleep(0.5 * (attempt + 1))
            else:
                return None
        self.error = None
        return out


def model_key(embedder: Embedder) -> str:
    return f"{embedder.model}@{RECIPE}"


def _pack(vec: list[float]) -> bytes:
    a = array("f", vec)
    if sys.byteorder != "little":
        a.byteswap()
    return a.tobytes()


def embed_library(conn: sqlite3.Connection, embedder: Embedder, on_progress: Callable[[int, int], None] | None = None, batch: int = 64, paper: str | None = None) -> dict[str, Any]:
    """Embed every unit that has no vector for this embedder and recipe, `batch` at a time,
    each batch committed on its own. Returns `{model, units, embedded, already, seconds}`.

    Idempotent: a second run embeds nothing. A batch the embedder does not answer, or answers
    with the wrong number of vectors, of mixed width or with a non-finite number, writes
    nothing; the run stops there and says so (`error`), keeping the batches already written,
    which are whole."""
    t0 = time.monotonic()
    ensure_schema(conn)
    _prepare(conn)
    key = model_key(embedder)
    extra, args = _filters(None, paper)
    total_units = conn.execute(f"SELECT COUNT(*) FROM nodes n WHERE {_UNIT_WHERE}{extra}", args).fetchone()[0]
    todo = conn.execute(
        f"""SELECT n.node_id, n.ancestry, n.text FROM nodes n
            LEFT JOIN vectors v ON v.node = n.node_id AND v.model = ?
            WHERE v.node IS NULL AND {_UNIT_WHERE}{extra} ORDER BY n.rowid""",
        [key, *args],
    ).fetchall()
    out: dict[str, Any] = {"model": key, "units": int(total_units), "embedded": 0, "already": int(total_units) - len(todo), "seconds": 0.0}
    dims: int | None = None
    row = conn.execute("SELECT dims FROM vectors WHERE model = ? LIMIT 1", (key,)).fetchone()
    if row is not None:
        dims = int(row[0])
    for i in range(0, len(todo), max(1, batch)):
        chunk = todo[i : i + batch]
        vecs = embedder.embed([doc_text(r) for r in chunk])
        problem = None
        if vecs is None:
            problem = f"the embedder did not answer: {getattr(embedder, 'error', None) or 'down'}"
        elif len(vecs) != len(chunk):
            problem = f"{len(vecs)} vectors for {len(chunk)} texts"
        else:
            widths = {len(v) for v in vecs}
            if len(widths) != 1 or (dims is not None and widths != {dims}):
                problem = f"vectors of width {sorted(widths)} where {dims or 'one width'} was expected"
            elif not all(math.isfinite(x) for v in vecs for x in v):
                problem = "a vector with a non-finite number"
        if problem:
            out["error"] = problem
            break
        dims = len(vecs[0])
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO vectors(node, model, dims, vec) VALUES (?,?,?,?)",
                [(r["node_id"], key, dims, _pack(v)) for r, v in zip(chunk, vecs)],
            )
        out["embedded"] += len(chunk)
        if on_progress:
            on_progress(out["embedded"], len(todo))
    out["seconds"] = round(time.monotonic() - t0, 3)
    return out


def status(conn: sqlite3.Connection, embedder: Embedder | None, ping: bool = True) -> dict[str, Any]:
    """How far a library is embedded, for the window's "N of M passages embedded":
    `{units, embedded, model, down, error}`. Counts only; no text is embedded. `down` comes
    from the embedder's `ping` (a one-second `GET /api/tags` for Ollama) when it has one and
    `ping` is on, and is False otherwise — unknown is not down."""
    _prepare(conn)
    key = model_key(embedder) if embedder is not None else None
    n_units = int(conn.execute(f"SELECT COUNT(*) FROM nodes n WHERE {_UNIT_WHERE}").fetchone()[0])
    embedded = 0
    if key and _has_vectors(conn):
        embedded = int(conn.execute(
            f"SELECT COUNT(*) FROM nodes n JOIN vectors v ON v.node = n.node_id AND v.model = ? WHERE {_UNIT_WHERE}", (key,)
        ).fetchone()[0])
    down, error = embedder is None, None if embedder is not None else "no embedder"
    if embedder is not None and ping and callable(getattr(embedder, "ping", None)):
        down = not embedder.ping()
        error = getattr(embedder, "error", None) if down else None
    return {"units": n_units, "embedded": embedded, "model": key, "down": down, "error": error}


# -- the matrix, once per store and model ---------------------------------------------------------------

_cache: dict[tuple[str, str], dict[str, Any]] = {}
_cache_lock = threading.Lock()


def _db_path(conn: sqlite3.Connection) -> str:
    for r in conn.execute("PRAGMA database_list"):
        if r[1] == "main":
            return r[2] or f"memory:{id(conn)}"
    return f"memory:{id(conn)}"


def _matrix(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    """Every vector of one model key as a normalised float32 matrix, with the node ids, roles
    and papers beside it. Loaded once per (store, model) and loaded again when the rows change:
    the signature is the count and the highest rowid, so a paper re-embedded after a rebuild
    (same count, new rows) is noticed."""
    if not _has_vectors(conn):
        return None
    gen = conn.execute("SELECT n FROM vectors_gen").fetchone() if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'vectors_gen'").fetchone() else None
    sig = (*conn.execute("SELECT COUNT(*), MAX(rowid) FROM vectors WHERE model = ?", (key,)).fetchone(), gen[0] if gen else None)
    if not sig[0]:
        return None
    ck = (_db_path(conn), key)
    with _cache_lock:
        hit = _cache.get(ck)
        if hit is not None and hit["sig"] == sig:
            return hit
    _prepare(conn)
    # only units: a vector written for a node the unit rule now refuses (a reference entry, known
    # as one only once the reference list was linked) is never a hit
    rows = conn.execute(
        f"SELECT v.node, v.dims, v.vec, n.role, n.paper FROM vectors v JOIN nodes n ON n.node_id = v.node WHERE v.model = ? AND {_UNIT_WHERE} ORDER BY v.rowid",
        (key,),
    ).fetchall()
    if not rows:
        return None
    dims = rows[0]["dims"]
    rows = [r for r in rows if r["dims"] == dims]
    m = np.frombuffer(b"".join(bytes(r["vec"]) for r in rows), dtype="<f4").reshape(len(rows), dims).astype(np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    m = m / np.where(norms == 0, 1.0, norms)
    entry = {
        "sig": sig, "dims": dims, "matrix": m,
        "ids": [r["node"] for r in rows],
        "roles": np.array([r["role"] for r in rows], dtype=object),
        "papers": np.array([r["paper"] for r in rows], dtype=object),
    }
    with _cache_lock:
        _cache[ck] = entry
    return entry


# -- the search --------------------------------------------------------------------------------------------

STOP = frozenset(
    ["the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are", "was", "were", "be", "by", "with", "as", "at", "from", "that", "this", "it", "its", "has", "have", "had", "do", "does", "did", "what", "which", "who", "how", "why", "when", "where", "any", "anyone", "used", "use", "using", "been", "than", "into", "their", "there"]
)
_SPLIT = re.compile(r"[^a-z0-9µ.%/-]+")
_TRIM = re.compile(r"^[.\-/]+|[.\-/]+$")


def fts_query(question: str) -> str:
    """A question as FTS5 wants it: the words, quoted, any of them — `src/db.ts` ftsQuery."""
    words: list[str] = []
    for w in _SPLIT.split(question.lower()):
        w = _TRIM.sub("", w)
        if len(w) > 1 and w not in STOP and w not in words:
            words.append(w)
    return " OR ".join(f'"{w.replace(chr(34), "")}"' for w in words)


class Hits(list):
    """A search's answer: the fused hits, best first, and how the meaning side fared —
    `meaning` is `ok`, `down` (the embedder did not answer), `empty` (no vectors for this
    model yet) or `mismatch` (the question's vector is not the stored vectors' width)."""

    meaning: str = "ok"
    error: str | None = None
    counts: dict[str, int]


def _by_words(conn: sqlite3.Connection, question: str, per_list: int, roles: Any, paper: str | None) -> list[tuple[str, float]]:
    match = fts_query(question)
    if not match:
        return []
    extra, args = _filters(roles, paper)
    try:
        rows = conn.execute(
            f"""SELECT n.node_id, bm25(nodes_fts) AS s FROM nodes_fts JOIN nodes n ON n.rowid = nodes_fts.rowid
                WHERE nodes_fts MATCH ? AND {_UNIT_WHERE}{extra} ORDER BY s LIMIT ?""",
            [match, *args, per_list],
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(r["node_id"], -float(r["s"])) for r in rows]  # bm25 is lower-is-better; every ranking here reads one way


def search(conn: sqlite3.Connection, question: str, embedder: Embedder | None, k: int = 10, per_list: int = 50, roles: Iterable[str] | None = None, paper: str | None = None) -> Hits:
    """The units nearest a question, through the words and through the meaning, fused by
    reciprocal rank (K = 60): `[{node_id, score, ranks: {words, meaning}}]`, best first, at
    most `k`. When the embedder is down, or has embedded nothing yet, the answer is the words
    alone and `Hits.meaning` says why."""
    _prepare(conn)
    roles = list(roles or [])
    out = Hits()
    words = _by_words(conn, question, per_list, roles, paper)
    meaning: list[tuple[str, float]] = []
    key = model_key(embedder) if embedder is not None else None
    mat = _matrix(conn, key) if key else None
    if embedder is None:
        out.meaning = "down"
        out.error = "no embedder"
    elif mat is None:
        out.meaning = "empty"
    else:
        qv = embedder.embed([query_text(question)])
        if not qv:
            out.meaning = "down"
            out.error = getattr(embedder, "error", None) or "the embedder did not answer"
        elif len(qv[0]) != mat["dims"]:
            out.meaning = "mismatch"
            out.error = f"question vector of width {len(qv[0])}, stored vectors {mat['dims']}"
        else:
            q = np.asarray(qv[0], dtype=np.float32)
            q /= float(np.linalg.norm(q)) or 1.0
            scores = mat["matrix"] @ q
            mask = np.ones(len(scores), dtype=bool)
            if roles:
                mask &= np.isin(mat["roles"], roles)
            if paper:
                mask &= mat["papers"] == paper
            idx = np.flatnonzero(mask)
            if idx.size:
                take = min(per_list, idx.size)
                top = idx[np.argpartition(-scores[idx], take - 1)[:take]]
                top = top[np.argsort(-scores[top], kind="stable")]
                meaning = [(mat["ids"][i], float(scores[i])) for i in top]
    fused: dict[str, dict[str, Any]] = {}
    for name, ranking in (("words", words), ("meaning", meaning)):
        for i, (nid, _) in enumerate(ranking):
            e = fused.setdefault(nid, {"node_id": nid, "score": 0.0, "ranks": {}})
            e["score"] += 1.0 / (RRF_K + i + 1)
            e["ranks"][name] = i + 1
    ranked = sorted(fused.values(), key=lambda e: (-e["score"], min(e["ranks"].values()), e["node_id"]))
    for e in ranked[:k]:
        e["score"] = round(e["score"], 6)
        out.append(e)
    out.counts = {"words": len(words), "meaning": len(meaning), "fused": len(fused), "vectors": int(mat["matrix"].shape[0]) if mat else 0}
    return out


# -- hydration ----------------------------------------------------------------------------------------------------


def _row(conn: sqlite3.Connection, node_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,)).fetchone()


def _enclosing_section(conn: sqlite3.Connection, r: sqlite3.Row) -> sqlite3.Row | None:
    """The nearest section above a node (the node itself when it is one)."""
    seen = 0
    while r is not None and seen < 64:
        if r["type"] == "section":
            return r
        if r["parent"] is None:
            return None
        r = _row(conn, r["parent"])
        seen += 1
    return None


def _brief(r: sqlite3.Row) -> dict[str, Any]:
    return {"node_id": r["node_id"], "type": r["type"], "role": r["role"], "page": r["page"], "text": r["text"]}


def _paragraphs_under(conn: sqlite3.Connection, node_id: str) -> list[sqlite3.Row]:
    """The paragraphs and list items under a section, in reading order (rowid: the tree is
    written in its own pre-order walk)."""
    return conn.execute(
        # CROSS JOIN keeps `sub` the outer loop, so each step is a probe of nodes_parent; left to
        # itself the planner scanned nodes, 47 ms a method on a library of 16,000 nodes
        """WITH RECURSIVE sub(id) AS (SELECT ? UNION ALL SELECT n.node_id FROM sub CROSS JOIN nodes n ON n.parent = sub.id)
           SELECT n.node_id, n.text, n.page FROM sub CROSS JOIN nodes n ON n.node_id = sub.id
           WHERE n.type IN ('paragraph', 'list_item') AND n.text != '' ORDER BY n.rowid""",
        (node_id,),
    ).fetchall()


def _marks(evidence: str | None, detail: str | None) -> list[str]:
    """The marks an edge was made on, as edges.py wrote them: "compressive modulus, calcein", or
    a caption's "Figure 4: calcein, live/dead". A pointer's "Section 2.3" names no mark."""
    if evidence not in ("terms", "caption") or not detail:
        return []
    if evidence == "caption" and ":" in detail:
        detail = detail.split(":", 1)[1]
    return [t.strip().lower() for t in detail.split(",") if t.strip()]


def choose_paragraph(paragraphs: list[sqlite3.Row], finding_text: str, marks: list[str]) -> tuple[sqlite3.Row | None, list[str]]:
    """The paragraph of a method a finding rests on, and the terms that chose it: the marks the
    edge was made on weigh three times a shared word, and a shared word weighs less the more of
    the method's paragraphs say it. None when nothing is shared that one paragraph has more of
    than the rest — the method is then given from its start, as a whole."""
    if len(paragraphs) == 1:
        return paragraphs[0], []
    if not paragraphs:
        return None, []
    own = [terms_of(p["text"]) for p in paragraphs]
    df: dict[str, int] = {}
    for ts in own:
        for t in ts:
            df[t] = df.get(t, 0) + 1
    wanted = terms_of(finding_text)
    best: tuple[float, int, list[str]] | None = None
    for i, ts in enumerate(own):
        hit_marks = [m for m in marks if m in ts]
        shared = [t for t in wanted & ts if df[t] < len(paragraphs)]  # a word every paragraph has tells them apart from nothing
        score = 3 * sum(2 if " " in m else 1 for m in hit_marks) + sum((2 if " " in t else 1) / df[t] for t in shared)
        if score > 0 and (best is None or score > best[0]):
            best = (score, i, hit_marks + sorted((t for t in shared if t not in hit_marks), key=lambda t: (df[t], " " not in t, t)))
    if best is None or (best[0] < 1.0 and not any(m in own[best[1]] for m in marks)):
        return None, []
    return paragraphs[best[1]], best[2][:6]


def _method(conn: sqlite3.Connection, dst: str, finding_text: str = "", marks: list[str] | None = None) -> dict[str, Any] | None:
    """A method as hydration gives it: its heading and path, and, from a subsection of several
    paragraphs, the one the finding rests on (`paragraph`, with the terms that chose it in
    `matched`) rather than whatever the subsection opens with."""
    r = _row(conn, dst)
    if r is None:
        return None
    ancestry = json.loads(r["ancestry"] or "[]")
    paragraph, matched = None, []
    if r["type"] == "section":
        heading = r["heading"]
        path = [*ancestry, heading] if heading else ancestry
        paras = _paragraphs_under(conn, dst)
        whole = " ".join(p["text"] for p in paras)
        if finding_text or marks:
            paragraph, matched = choose_paragraph(paras, finding_text, marks or [])
        text = paragraph["text"] if paragraph is not None else whole
        page = (paragraph["page"] if paragraph is not None else None) or r["page"]
        count = len(paras)
    else:
        sec = _enclosing_section(conn, r)
        heading = sec["heading"] if sec is not None else None
        whole = text = r["text"]
        path = ancestry
        page = r["page"]
        count = 1
    return {
        "node_id": dst, "type": r["type"], "heading": heading, "ancestry": path, "page": page,
        "text": _cut(text, METHOD_CHARS), "chars": len(whole),
        "paragraph": paragraph["node_id"] if paragraph is not None else None, "matched": matched, "paragraphs": count,
        "general": (r["canonical"] or "") in GENERAL_METHODS,
    }


def best_paragraph(conn: sqlite3.Connection, finding_id: str, method_id: str) -> str | None:
    """The paragraph of `method_id` hydration would show for `finding_id` — the chooser the link
    labels (truth.py) are measured against."""
    f = _row(conn, finding_id)
    e = conn.execute("SELECT evidence, detail FROM edges WHERE src = ? AND dst = ? AND kind = 'measured_by'", (finding_id, method_id)).fetchone()
    m = _method(conn, method_id, f["text"] if f is not None else "", _marks(e["evidence"], e["detail"]) if e is not None else [])
    return m["paragraph"] if m is not None else None


def _methods(conn: sqlite3.Connection, hit: sqlite3.Row, anchor: sqlite3.Row, section: sqlite3.Row | None,
             limit: int = METHODS_SHOWN) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """What the hit was measured by, from the `measured_by` rows and nothing else, as
    `(methods, general)`: the methods particular to the finding, strongest edge first, and apart
    from them the parts every finding shares (statistics, materials: `GENERAL_METHODS`).

    The hit's own edges come first (`via: hit`). While none of them is particular, a caption
    takes the edges of the paragraphs that cite its figure (`via: figure`), and a hit in a
    results lane the edges of the other paragraphs in its section (`via: section`), most shared
    first — not a discussion or introduction hit: its section is the whole Discussion, and on
    the looped-ligament library pooling it named "Statistical analysis" first. A fallback edge
    says which nodes it came from (`sources`), so it reads as what it is: the section's methods,
    not a claim about this paragraph."""
    own = conn.execute("SELECT dst, evidence, detail, score FROM edges WHERE src = ? AND kind = 'measured_by' ORDER BY score DESC, dst", (hit["node_id"],)).fetchall()
    found: list[tuple[str, dict[str, Any]]] = [(r["dst"], {"evidence": r["evidence"], "detail": r["detail"], "score": r["score"], "via": "hit"}) for r in own]
    built: dict[str, dict[str, Any] | None] = {}
    finding_text = hit["text"] or ""

    def build(dst: str, meta: dict[str, Any]) -> dict[str, Any] | None:
        if dst not in built:
            m = _method(conn, dst, finding_text, _marks(meta.get("evidence"), meta.get("detail")))
            built[dst] = {**m, **meta} if m is not None else None
        return built[dst]

    def particular() -> bool:
        return any((m := build(d, meta)) is not None and not m["general"] for d, meta in found)

    if not particular() and anchor["node_id"] != hit["node_id"]:
        found += [x for x in _shared(conn.execute(
            """SELECT m.src, m.dst, m.evidence, m.detail, m.score FROM edges c JOIN edges m ON m.src = c.src AND m.kind = 'measured_by'
               WHERE c.dst = ? AND c.kind = 'cites_figure'""", (anchor["node_id"],)).fetchall(), "figure") if x[0] not in {d for d, _ in found}]
    if not particular() and section is not None and hit["role"] in FINDING_LANES:
        found += [x for x in _shared(conn.execute(
            """SELECT e.src, e.dst, e.evidence, e.detail, e.score FROM edges e JOIN nodes n ON n.node_id = e.src
               WHERE n.parent = ? AND e.kind = 'measured_by' AND e.src != ?""", (section["node_id"], hit["node_id"])).fetchall(), "section") if x[0] not in {d for d, _ in found}]
    methods: list[dict[str, Any]] = []
    general: list[dict[str, Any]] = []
    for dst, meta in found:
        if len(methods) >= limit and len(general) >= GENERAL_SHOWN:
            break
        m = build(dst, meta)
        if m is None:
            continue
        if m["general"]:
            if len(general) < GENERAL_SHOWN:
                general.append(m)
        elif len(methods) < limit:
            m["described_in"] = described_elsewhere(conn, dst, limit=ELSEWHERE_SHOWN)
            methods.append(m)
    return methods, general


def _findings_measured(conn: sqlite3.Connection, r: sqlite3.Row, limit: int = FINDINGS_SHOWN) -> dict[str, Any] | None:
    """For a hit in a methods lane, the edges walked the other way: the findings measured by the
    method part it sits in (itself, or the nearest section above it that edges point at), most
    strongly linked first, with how many there are in all."""
    node, steps = r, 0
    while node is not None and node["role"] == "methods" and steps < 16:
        rows = conn.execute(
            """SELECT e.src, e.evidence, e.detail, e.score, n.text, n.page, n.role, n.ancestry FROM edges e JOIN nodes n ON n.node_id = e.src
               WHERE e.dst = ? AND e.kind = 'measured_by' ORDER BY e.score DESC, n.rowid""",
            (node["node_id"],),
        ).fetchall()
        if rows:
            return {
                "method": node["node_id"], "heading": node["heading"], "total": len(rows),
                "findings": [
                    {"node_id": x["src"], "role": x["role"], "page": x["page"], "ancestry": json.loads(x["ancestry"] or "[]"),
                     "text": _cut(x["text"] or "", FINDING_CHARS), "evidence": x["evidence"], "detail": x["detail"], "score": x["score"]}
                    for x in rows[:limit]
                ],
            }
        node = _row(conn, node["parent"]) if node["parent"] else None
        steps += 1
    return None


def _shared(rows: list[sqlite3.Row], via: str) -> list[tuple[str, dict[str, Any]]]:
    by: dict[str, dict[str, Any]] = {}
    for r in rows:
        e = by.setdefault(r["dst"], {"evidence": set(), "detail": r["detail"], "score": 0.0, "via": via, "sources": []})
        e["evidence"].add(r["evidence"])
        e["score"] = max(e["score"], float(r["score"] or 0.0))
        if r["src"] not in e["sources"]:
            e["sources"].append(r["src"])
    ranked = sorted(by.items(), key=lambda kv: (-len(kv[1]["sources"]), -kv[1]["score"], kv[0]))
    for _, e in ranked:
        e["evidence"] = ", ".join(sorted(e["evidence"]))
    return ranked


def _caption_of(conn: sqlite3.Connection, figure_id: str) -> str:
    rows = conn.execute("SELECT text FROM nodes WHERE parent = ? AND type = 'caption' ORDER BY ordinal", (figure_id,)).fetchall()
    return " ".join(r["text"] for r in rows if r["text"])


_CAPTION = re.compile(r"^\s*(?:fig(?:ure)?|scheme)\.?\s*S?\d+", re.I)


def _captioned(conn: sqlite3.Connection, r: sqlite3.Row) -> sqlite3.Row | None:
    """The figure a caption belongs to: its parent when the reader hung it there, else — a
    paragraph that opens "Figure 2" — the large picture on its page nearest it."""
    if r["type"] == "caption" and r["parent"]:
        fig = conn.execute("SELECT node_id, type FROM nodes WHERE node_id = ? AND type IN ('picture', 'chart')", (r["parent"],)).fetchone()
        if fig is not None:
            return fig
    if not _CAPTION.match(r["text"] or "") or r["page"] is None or r["bbox_t"] is None:
        return None
    best, gap = None, None
    for f in conn.execute("SELECT node_id, type, bbox_l, bbox_t, bbox_r, bbox_b FROM nodes WHERE paper = ? AND page = ? AND type IN ('picture', 'chart') AND bbox_l IS NOT NULL", (r["paper"], r["page"])):
        if f["bbox_r"] - f["bbox_l"] < 72 or abs(f["bbox_b"] - f["bbox_t"]) < 72:
            continue  # a logo
        top, bottom = min(f["bbox_t"], f["bbox_b"]), max(f["bbox_t"], f["bbox_b"])
        ct, cb = min(r["bbox_t"], r["bbox_b"]), max(r["bbox_t"], r["bbox_b"])
        d = 0.0 if ct <= bottom and cb >= top else min(abs(ct - bottom), abs(cb - top))  # beside it, or above or below
        if gap is None or d < gap:
            best, gap = f, d
    return best if best is not None and gap is not None and gap <= 60 else None


def _figure_data(conn: sqlite3.Connection, figure: str, label: str | None, text: str) -> list[dict[str, Any]]:
    """The plots read from a cited figure (figures.py), the panels the passage names first and
    marked `cited` — "Figure 2B", "Fig. 3(a)"."""
    from .figures import of_figure

    plots = [p for p in of_figure(conn, figure) if p["status"] == "read"]
    if not plots:
        return []
    num = (label or "").split()[-1] if label else ""
    named = {m.group(1).upper() for m in re.finditer(rf"\bFig(?:ure)?s?\.?\s*{re.escape(num)}\s*\(?([A-Ha-h])(?![a-z])", text)} if num else set()
    for p in plots:
        p["cited"] = bool(p["panel"] and p["panel"] in named)
    plots.sort(key=lambda p: not p["cited"])
    return plots


def hydrate(conn: sqlite3.Connection, node_id: str, before: int = 1, after: int = 1) -> dict[str, Any]:
    """One unit with its context, every piece a row:
    `{hit, paper, section, before, after, methods, general, findings, described_in, figures,
    cites}` — the node; its paper's record; the section it sits in; the paragraphs just before
    and after it under the same parent (a caption's are its figure's); the methods it was
    measured by, each with the paragraph it rests on and where else it is described, and the
    statistics and materials apart (`_methods`); for a methods hit, the findings measured by
    its method (`_findings_measured`) and where its own procedure is described; the figures and
    tables it cites, with their captions; the reference entries it cites."""
    r = _row(conn, node_id)
    if r is None:
        return {"error": f"no node {node_id}"}
    anchor = r
    if r["type"] == "caption" and r["parent"] is not None:
        p = _row(conn, r["parent"])
        if p is not None and p["type"] in ("picture", "table"):
            anchor = p  # a caption sits in its figure; the figure sits in the prose
    section = _enclosing_section(conn, _row(conn, anchor["parent"]) if anchor["parent"] else None)
    d = node_row(conn, node_id) or {}
    hit = {k: d.get(k) for k in ("node_id", "paper", "type", "role", "ancestry", "text", "page", "bbox")}
    hit["heading"] = d.get("heading") or (section["heading"] if section is not None else None)
    if anchor is not r:
        hit["figure"] = anchor["node_id"]
    p = conn.execute("SELECT key, title, year, journal, doi, type FROM papers WHERE key = ?", (r["paper"],)).fetchone()
    out: dict[str, Any] = {
        "hit": hit,
        "paper": dict(p) if p else {"key": r["paper"]},
        "section": {"node_id": section["node_id"], "heading": section["heading"], "role": section["role"], "canonical": section["canonical"]} if section is not None else None,
        "before": [], "after": [],
    }
    if anchor["parent"] is not None:
        sibs = conn.execute(
            f"SELECT * FROM nodes WHERE parent = ? AND type IN ({', '.join('?' for _ in PARAGRAPH_LIKE)}) AND text != '' ORDER BY ordinal",
            (anchor["parent"], *PARAGRAPH_LIKE),
        ).fetchall()
        prev = [s for s in sibs if s["ordinal"] < anchor["ordinal"]]
        nxt = [s for s in sibs if s["ordinal"] > anchor["ordinal"]]
        out["before"] = [_brief(s) for s in (prev[-before:] if before > 0 else [])]
        out["after"] = [_brief(s) for s in nxt[: max(0, after)]]
    out["methods"], out["general"] = _methods(conn, r, anchor, section)
    # a hit in the methods is asked the other way round: what was measured by the method it is
    # part of, and where its own procedure is described when it says "as previously described"
    out["findings"] = _findings_measured(conn, r) if r["role"] == "methods" else None
    out["described_in"] = described_elsewhere(conn, node_id, limit=ELSEWHERE_SHOWN) if r["role"] == "methods" else []
    out["figures"] = [
        {"node_id": f["dst"], "type": f["type"], "label": f["detail"], "caption": _cut(_caption_of(conn, f["dst"]), CAPTION_CHARS),
         "data": _figure_data(conn, f["dst"], f["detail"], r["text"] or "")}
        for f in conn.execute(
            "SELECT e.dst, e.detail, n.type FROM edges e JOIN nodes n ON n.node_id = e.dst WHERE e.src = ? AND e.kind = 'cites_figure' ORDER BY n.ordinal",
            (node_id,),
        ).fetchall()
    ]
    fig = _captioned(conn, r)
    if fig is not None:  # a caption hit: the numbers of the figure it captions, first
        if all(f["node_id"] != fig["node_id"] for f in out["figures"]):
            out["figures"].insert(0, {"node_id": fig["node_id"], "type": fig["type"], "label": None, "caption": _cut(r["text"] or "", CAPTION_CHARS),
                                      "data": _figure_data(conn, fig["node_id"], None, r["text"] or "")})
    out["cites"] = [{**c, "text": _cut(c.get("text") or "", CITE_CHARS)} for c in cites_of(conn, node_id)]
    return out


# -- the whole answer --------------------------------------------------------------------------------------------


def query(conn: sqlite3.Connection, question: str, embedder: Embedder | None, k: int = 8, before: int = 1, after: int = 1, **kw: Any) -> dict[str, Any]:
    """`search`, then `hydrate` each hit: `{question, hits: [{rank, score, ranks, hit, paper,
    section, before, after, methods, general, findings, described_in, figures, cites, also?}],
    embedder, counts, seconds}`.

    A hit that already sits in a better hit's context — one of the paragraphs just before or
    after it — is not given its own place; the better hit lists its node id under `also`
    (present only when there is one), so nothing found is hidden."""
    t0 = time.monotonic()
    found = search(conn, question, embedder, k=max(3 * k, k + 16), **kw)
    hits: list[dict[str, Any]] = []
    owner: dict[str, dict[str, Any]] = {}
    folded = 0
    for e in found:
        if e["node_id"] in owner:
            owner[e["node_id"]].setdefault("also", []).append(e["node_id"])
            folded += 1
            continue
        if len(hits) >= k:
            continue
        h = hydrate(conn, e["node_id"], before=before, after=after)
        if "error" in h:
            continue
        entry = {"rank": len(hits) + 1, "score": e["score"], "ranks": e["ranks"], **h}
        hits.append(entry)
        owner.setdefault(e["node_id"], entry)
        for s in (*h["before"], *h["after"]):
            owner.setdefault(s["node_id"], entry)
    return {
        "question": question,
        "hits": hits,
        "embedder": {
            "model": model_key(embedder) if embedder is not None else None,
            "meaning": found.meaning,
            "down": found.meaning == "down",
            "error": found.error,
        },
        "counts": {**found.counts, "hits": len(hits), "also": folded},
        "seconds": round(time.monotonic() - t0, 3),
    }


# -- the command line ------------------------------------------------------------------------------------------------


def _print_answer(a: dict[str, Any]) -> None:
    e = a["embedder"]
    print(f"{a['question']!r}: {len(a['hits'])} hits in {a['seconds']}s · meaning {e['meaning']}{' (' + e['error'] + ')' if e.get('error') else ''} · {a['counts']}")
    for h in a["hits"]:
        where = " > ".join(h["hit"]["ancestry"] or []) or (h["section"] or {}).get("heading") or ""
        print(f"\n#{h['rank']} {h['score']:.4f} {h['ranks']}  [{h['hit']['role']}] {h['paper'].get('title', '')[:70]} ({h['paper'].get('year') or ''})")
        print(f"   {where[:100]} · p. {h['hit']['page']}")
        print(f"   {h['hit']['text'][:240]}")
        for m in h["methods"]:
            chosen = f" [paragraph: {', '.join(m['matched'][:3])}]" if m.get("paragraph") and m.get("matched") else ""
            print(f"   method ({m['via']}, {m['evidence']}: {m['detail']}): {m['heading']}{chosen} — {m['text'][:120]}")
            for d in m.get("described_in") or []:
                where = f"{d['paper']['title'][:60]} ({d['paper'].get('year') or ''})" if d.get("paper") else f"ref {d['ref_no']}, not in the library"
                print(f"     described in {where}: {(d.get('method') or {}).get('heading') or ''}")
        if h.get("general"):
            print(f"   also: {', '.join(m['heading'] or '' for m in h['general'])}")
        if h.get("findings"):
            print(f"   findings measured here: {h['findings']['total']} — " + " | ".join(f['text'][:60] for f in h['findings']['findings'][:3]))
        for f in h["figures"]:
            print(f"   figure {f['label']}: {f['caption'][:100]}")
        if h["cites"]:
            print(f"   cites {len(h['cites'])}: " + "; ".join((c.get('first_author') or '') + ' ' + (c.get('year') or '') for c in h["cites"][:5]))
        if h.get("also"):
            print(f"   also in its context: {h['also']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="litrag_parser.retrieve", description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", required=True, help="a library's store.sqlite")
    ap.add_argument("--embed", action="store_true", help="embed every unit that has no vector yet")
    ap.add_argument("--query", help="a question")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--role", action="append", default=[], help="only units in this lane (repeatable)")
    ap.add_argument("--paper", help="only this paper's units")
    ap.add_argument("--json", action="store_true", help="the answer as JSON")
    ap.add_argument("--url", help="Ollama (default LITRAG_OLLAMA_URL or http://127.0.0.1:11434)")
    ap.add_argument("--model", help="embedder (default LITRAG_LANES_MODEL or nomic-embed-text)")
    args = ap.parse_args(argv)
    if not args.embed and not args.query:
        ap.print_help()
        return 2
    from pathlib import Path

    conn = open_store(Path(args.store).expanduser())
    embedder = OllamaEmbedder(url=args.url, model=args.model)
    if args.embed:
        r = embed_library(conn, embedder, on_progress=lambda d, t: print(f"  embedded {d}/{t}", file=sys.stderr), paper=args.paper)
        print(json.dumps(r, ensure_ascii=False))
        if "error" in r:
            return 1
    if args.query:
        a = query(conn, args.query, embedder, k=args.k, roles=args.role or None, paper=args.paper)
        if args.json:
            print(json.dumps(a, ensure_ascii=False, default=str))
        else:
            _print_answer(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
