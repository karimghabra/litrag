"""Finding papers and fetching them: Europe PMC's search, a table of candidates, XML first.

A search is asked of Europe PMC (`resultType=core`, a cursor for the next page) and every hit
becomes a row in `candidates`, in the library's own store, so a person can `SELECT` what was
found, what was fetched and what still wants a PDF. A candidate is found once: unique by DOI, by
PMID and by PMCID, so a search run twice adds nothing twice, and a candidate already seen keeps
its status (invariant 4).

Fetching goes the way the pairs taught: the JATS full text from the REST service first, because
it is the paper's own structure; else the publisher's PDF from EBI's bulk open-access area
(`ftp.ebi.ac.uk/pub/databases/pmc/pdf/OA/PMCxxxx<block>/<PMCID>.zip`, the same place the corpus
scripts fetch from — the website's `?pdf=render` links sit behind a bot check and are left
alone); else the candidate `needs-pdf`, with the links a person can follow to get it by hand
and drop it into the app. What is fetched lands in the library's inbox; the worker's `ingest`
files it (DOI, then PMID, then hash) and `reconcile` marks the candidate `ingested`.

The only hosts asked are Europe PMC's (EBI's). Both bases can be pointed elsewhere, for tests
and the end-to-end harness: `LITRAG_EPMC_URL` for the REST base, `LITRAG_EPMC_PDF_URL` for the
bulk PDF base.

    python -m litrag_parser.acquire --lib DIR --search "hydrogel cartilage" [--size 25]
    python -m litrag_parser.acquire --lib DIR --fetch 1 2 3
    python -m litrag_parser.acquire --lib DIR --list [--status needs-pdf] | --wanted
"""

from __future__ import annotations

import argparse
import html
import io
import json
import math
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable

from .library import Library, now_iso, safe_key

REST = "https://www.ebi.ac.uk/europepmc/webservices/rest"
BULK_PDF = "https://ftp.ebi.ac.uk/pub/databases/pmc/pdf/OA"
USER_AGENT = "litrag (local research tool; one request at a time)"

STATUSES = ("found", "staged", "fetching", "fetched", "needs-pdf", "ingested", "failed", "dismissed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
  cand_id INTEGER PRIMARY KEY,
  query TEXT,                       -- the search that first found it
  pmid TEXT, pmcid TEXT, doi TEXT,  -- doi lowercased, pmcid upper-cased
  title TEXT, authors TEXT, journal TEXT, year TEXT, abstract TEXT,
  pub_types TEXT,                   -- Europe PMC's publication types, "; "-joined
  cited_by INTEGER,
  is_open_access INTEGER, has_xml INTEGER, has_pdf INTEGER,
  status TEXT NOT NULL,             -- found | staged | fetching | fetched | needs-pdf | ingested | failed | dismissed
  paper_key TEXT,                   -- the papers row it became, once it is one
  file TEXT,                        -- the inbox file a fetch wrote
  error TEXT,
  found_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS candidates_doi ON candidates(doi) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS candidates_pmid ON candidates(pmid) WHERE pmid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS candidates_pmcid ON candidates(pmcid) WHERE pmcid IS NOT NULL;
CREATE INDEX IF NOT EXISTS candidates_status ON candidates(status);
"""

#: The columns a hit fills, in the order they are inserted.
_FIELDS = ("pmid", "pmcid", "doi", "title", "authors", "journal", "year", "abstract", "pub_types", "cited_by", "is_open_access", "has_xml", "has_pdf")


class AcquireError(RuntimeError):
    """Europe PMC could not be asked, or answered with something that is not an answer."""


def rest_base(base: str | None = None) -> str:
    return (base or os.environ.get("LITRAG_EPMC_URL") or REST).rstrip("/")


def pdf_base(base: str | None = None) -> str:
    return (base or os.environ.get("LITRAG_EPMC_PDF_URL") or BULK_PDF).rstrip("/")


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---------------------------------------------------------------- search


_TAG = re.compile(r"<[^>]+>")


def plain_text(text: Any) -> str | None:
    """Europe PMC hands titles back with their markup, sometimes escaped twice: `&lt;i&gt;In
    Vivo&lt;/i&gt;`. Unescaped, untagged, one line."""
    if not text:
        return None
    t = _TAG.sub("", html.unescape(html.unescape(str(text))))
    return " ".join(t.split()) or None


def _flag(v: Any) -> bool:
    return str(v or "").upper() == "Y"


def normalise_hit(h: dict[str, Any]) -> dict[str, Any]:
    """One `core` result as a candidate: identifiers normalised (DOI lowercased, PMCID upper),
    the journal from `journalInfo.journal.title` (a `core` result carries no `journalTitle`; a
    `lite` one does), the publication types from `pubTypeList`. `has_xml` is the practical test
    for `fullTextXML`: a PMCID, in Europe PMC and open access. `has_pdf` is Europe PMC's own
    flag — it means a rendered PDF exists, not that the bulk area holds one."""
    pmcid = str(h.get("pmcid") or "").strip().upper() or None
    doi = str(h.get("doi") or "").strip().lower() or None
    pmid = str(h.get("pmid") or "").strip() or None
    journal = ((h.get("journalInfo") or {}).get("journal") or {}).get("title") or h.get("journalTitle")
    types = (h.get("pubTypeList") or {}).get("pubType")
    if isinstance(types, str):
        types = [types]
    if not types:
        types = [p.strip() for p in str(h.get("pubType") or "").split(";") if p.strip()]
    title = plain_text(h.get("title"))
    oa, in_epmc = _flag(h.get("isOpenAccess")), _flag(h.get("inEPMC"))
    try:
        cited = int(h.get("citedByCount") or 0)
    except (TypeError, ValueError):
        cited = 0
    return {
        "source": "europepmc",
        "epmc_source": h.get("source"),
        "id": str(h.get("id") or "") or None,
        "pmid": pmid,
        "pmcid": pmcid,
        "doi": doi,
        "title": title.rstrip(".") if title else None,
        "authors": (str(h.get("authorString") or "").strip().rstrip(".") or None),
        "journal": journal or None,
        "year": str(h.get("pubYear")) if h.get("pubYear") else None,
        "abstract": plain_text(h.get("abstractText")),
        "is_open_access": oa,
        "in_pmc": _flag(h.get("inPMC")),
        "in_epmc": in_epmc,
        "has_pdf": _flag(h.get("hasPDF")),
        "has_xml": bool(pmcid and in_epmc and oa),
        "cited_by": cited,
        "pub_types": list(types),
    }


def search(query: str, page_size: int = 25, cursor: str = "*", timeout: float = 20, *, base: str | None = None) -> dict[str, Any]:
    """One page of Europe PMC's search: `{hits, next_cursor, total}`. `next_cursor` is None on
    the last page. Raises `AcquireError` when the service cannot be asked."""
    if not query or not query.strip():
        raise AcquireError("a search needs a query")
    size = max(1, min(int(page_size), 1000))
    params = urllib.parse.urlencode({"query": query, "resultType": "core", "format": "json", "pageSize": size, "cursorMark": cursor or "*"})
    url = f"{rest_base(base)}/search?{params}"
    try:
        data = json.loads(_get(url, timeout).decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise AcquireError(f"Europe PMC search failed ({e.code})") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AcquireError(f"Europe PMC could not be reached: {e}") from e
    except json.JSONDecodeError as e:
        raise AcquireError("Europe PMC answered with something that is not JSON") from e
    results = (data.get("resultList") or {}).get("result") or []
    hits = [normalise_hit(h) for h in results]
    nxt = data.get("nextCursorMark")
    if not results or not nxt or nxt == cursor:
        nxt = None
    return {"hits": hits, "next_cursor": nxt, "total": int(data.get("hitCount") or 0)}


# ---------------------------------------------------------------- the table


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _row(conn: sqlite3.Connection, cand_id: int) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM candidates WHERE cand_id = ?", (cand_id,))
    r = cur.fetchone()
    return dict(zip([d[0] for d in cur.description], r)) if r is not None else None


def _values(hit: dict[str, Any]) -> dict[str, Any]:
    v = {k: hit.get(k) for k in _FIELDS}
    v["pub_types"] = "; ".join(hit.get("pub_types") or []) or None
    for k in ("is_open_access", "has_xml", "has_pdf"):
        v[k] = 1 if hit.get(k) else 0
    v["doi"] = (v["doi"] or "").lower() or None
    v["pmcid"] = (v["pmcid"] or "").upper() or None
    return v


def _existing(conn: sqlite3.Connection, v: dict[str, Any]) -> int | None:
    for col in ("doi", "pmid", "pmcid"):
        if v.get(col):
            r = conn.execute(f"SELECT cand_id FROM candidates WHERE {col} = ?", (v[col],)).fetchone()
            if r is not None:
                return int(r[0])
    if not (v.get("doi") or v.get("pmid") or v.get("pmcid")) and v.get("title"):
        r = conn.execute("SELECT cand_id FROM candidates WHERE doi IS NULL AND pmid IS NULL AND pmcid IS NULL AND title = ? AND COALESCE(year, '') = ?", (v["title"], v.get("year") or "")).fetchone()
        if r is not None:
            return int(r[0])
    return None


def upsert_candidate(conn: sqlite3.Connection, hit: dict[str, Any], *, query: str | None, now: str, status: str = "found") -> tuple[int, bool]:
    """A hit as a candidate, once. Returns `(cand_id, added)`. A candidate already seen keeps its
    status and its query; what it lacked is filled, and nothing it has is replaced."""
    v = _values(hit)
    cid = _existing(conn, v)
    if cid is None:
        cols = ("query", *_FIELDS, "status", "found_at", "updated_at")
        cur = conn.execute(f"INSERT INTO candidates({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", (query, *(v[k] for k in _FIELDS), status, now, now))
        return int(cur.lastrowid), True
    for col in _FIELDS:
        if v.get(col) is None:
            continue
        if col in ("doi", "pmid", "pmcid"):
            # an identifier another candidate already holds is that one's; two partial rows of one
            # paper stay two rather than one being overwritten by the other
            if conn.execute(f"SELECT 1 FROM candidates WHERE {col} = ? AND cand_id != ?", (v[col], cid)).fetchone():
                continue
        conn.execute(f"UPDATE candidates SET {col} = COALESCE({col}, ?) WHERE cand_id = ?", (v[col], cid))
    return cid, False


def _write_manifest(lib: Library, update: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Read library.json fresh, change it, write it the way `create_library` does."""
    try:
        manifest = json.loads(lib.manifest_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = dict(lib.manifest)
    update(manifest)
    tmp = lib.manifest_path.with_suffix(".json.part")
    tmp.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + "\n", "utf-8")
    os.replace(tmp, lib.manifest_path)
    lib.manifest = manifest
    return manifest


def record_search(lib: Library, conn: sqlite3.Connection, query: str, hits: Iterable[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    """File a search's hits as candidates (status `found`; one seen before keeps its own), note
    the search in the manifest's `queries`, and mark what the library already holds."""
    ensure_schema(conn)
    now = now_iso()
    hits = list(hits)
    ids: list[int] = []
    added = 0
    with conn:
        for h in hits:
            cid, new = upsert_candidate(conn, h, query=query, now=now)
            ids.append(cid)
            added += int(new)
    entry = {"query": query, "at": now, "total": int(total if total is not None else len(hits)), "added": added}
    _write_manifest(lib, lambda m: m.setdefault("queries", []).append(entry))
    ingested = reconcile(conn)
    return {"query": query, "at": now, "total": entry["total"], "added": added, "seen": len(hits) - added, "cand_ids": ids, "ingested": ingested}


def reconcile(conn: sqlite3.Connection) -> int:
    """Every candidate the library already holds, by DOI, PMID or PMCID, is `ingested`, with the
    key of the paper it became. Returns how many changed."""
    ensure_schema(conn)
    if not _has_table(conn, "papers"):
        return 0
    now = now_iso()
    n = 0
    with conn:
        rows = conn.execute("SELECT cand_id, doi, pmid, pmcid FROM candidates WHERE status != 'ingested' OR paper_key IS NULL").fetchall()
        for cid, doi, pmid, pmcid in rows:
            hit = None
            if doi:
                hit = conn.execute("SELECT key FROM papers WHERE lower(doi) = ?", (doi.lower(),)).fetchone()
            if hit is None and pmid:
                hit = conn.execute("SELECT key FROM papers WHERE pmid = ?", (pmid,)).fetchone()
            if hit is None and pmcid:
                hit = conn.execute("SELECT key FROM papers WHERE upper(pmcid) = ?", (pmcid.upper(),)).fetchone()
            if hit is None:
                continue
            conn.execute("UPDATE candidates SET status = 'ingested', paper_key = ?, error = NULL, updated_at = ? WHERE cand_id = ?", (hit[0], now, cid))
            n += 1
    return n


def links(row: dict[str, Any]) -> dict[str, str]:
    """Where a person can get a paper by hand: its DOI's resolver and its Europe PMC page."""
    out: dict[str, str] = {}
    if row.get("doi"):
        out["doi"] = f"https://doi.org/{row['doi']}"
    if row.get("pmid"):
        out["europepmc"] = f"https://europepmc.org/article/MED/{row['pmid']}"
    elif row.get("pmcid"):
        out["europepmc"] = f"https://europepmc.org/article/PMC/{row['pmcid']}"
    return out


def candidates(conn: sqlite3.Connection, status: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
    """The candidates, in the order found, each with its links; narrowed by status or query."""
    ensure_schema(conn)
    where, args = [], []
    if status:
        where.append("status = ?")
        args.append(status)
    if query:
        where.append("query = ?")
        args.append(query)
    cur = conn.execute(f"SELECT * FROM candidates{' WHERE ' + ' AND '.join(where) if where else ''} ORDER BY cand_id", args)
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        d["links"] = links(d)
        out.append(d)
    return out


def wanted(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The candidates that want a PDF from a person: nothing open was there to fetch."""
    return candidates(conn, status="needs-pdf")


def _set_status(conn: sqlite3.Connection, ids: Iterable[int], status: str, allowed_from: tuple[str, ...]) -> dict[str, Any]:
    ensure_schema(conn)
    now = now_iso()
    changed: list[int] = []
    with conn:
        for cid in ids:
            cur = conn.execute(f"UPDATE candidates SET status = ?, updated_at = ? WHERE cand_id = ? AND status IN ({', '.join('?' * len(allowed_from))})", (status, now, int(cid), *allowed_from))
            if cur.rowcount:
                changed.append(int(cid))
    return {"status": status, "changed": changed}


def dismiss(conn: sqlite3.Connection, ids: Iterable[int]) -> dict[str, Any]:
    """Set candidates aside. One already in the library stays `ingested`: that is a fact."""
    return _set_status(conn, ids, "dismissed", ("found", "staged", "needs-pdf", "failed", "fetched"))


def stage(conn: sqlite3.Connection, ids: Iterable[int]) -> dict[str, Any]:
    """Mark candidates to be fetched. A dismissed one can be taken back."""
    return _set_status(conn, ids, "staged", ("found", "needs-pdf", "failed", "dismissed"))


# ---------------------------------------------------------------- fetching


def file_key(row: dict[str, Any]) -> str:
    """The key the paper would be filed under, as a file name: DOI, then PMID, then PMCID."""
    key = f"doi:{row['doi']}" if row.get("doi") else f"pmid:{row['pmid']}" if row.get("pmid") else f"pmcid:{row['pmcid']}" if row.get("pmcid") else f"cand:{row['cand_id']}"
    return safe_key(key)


def pdf_url(pmcid: str, base: str | None = None) -> str:
    """The bulk open-access area files a paper's PDF, zipped, in blocks of ten thousand:
    PMC11457099 is `PMCxxxx1146/PMC11457099.zip`."""
    block = math.ceil(int(pmcid.upper().removeprefix("PMC")) / 10000)
    return f"{pdf_base(base)}/PMCxxxx{block}/{pmcid.upper()}.zip"


def _pdf_from(data: bytes, pmcid: str) -> bytes | None:
    """The PDF itself: the bytes when they are one, the paper's own PDF from a zip (the one named
    for it, else the largest), else None."""
    if data[:4] == b"%PDF":
        return data
    if data[:2] != b"PK":
        return None
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    pdfs = sorted((i for i in z.infolist() if i.filename.lower().endswith(".pdf")),
                  key=lambda i: (Path(i.filename).name.lower() != f"{pmcid.lower()}.pdf", -i.file_size))
    for info in pdfs:
        body = z.read(info)
        if body[:4] == b"%PDF":
            return body
    return None


def _save(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def _update(conn: sqlite3.Connection, cid: int, **cols: Any) -> None:
    cols["updated_at"] = now_iso()
    with conn:
        conn.execute(f"UPDATE candidates SET {', '.join(f'{k} = ?' for k in cols)} WHERE cand_id = ?", (*cols.values(), cid))


def fetch_one(lib: Library, conn: sqlite3.Connection, cand_id: int, *, timeout: float = 90, base: str | None = None,
              pdf: str | None = None, on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """One candidate: its JATS, else its bulk PDF, else `needs-pdf`. Never raises for the
    candidate; what went wrong is the answer's `error` and the row's."""
    say = on_progress or (lambda e: None)
    row = _row(conn, cand_id)
    if row is None:
        return {"cand_id": cand_id, "status": None, "path": None, "format": None, "error": "no such candidate"}
    if row["status"] in ("ingested", "dismissed"):
        return {"cand_id": cand_id, "status": row["status"], "path": None, "format": None, "error": None}
    if row["status"] == "fetched" and row["file"] and (lib.inbox_dir / row["file"]).exists():
        # fetched before and still waiting in the inbox: the same file, not a second download
        path = lib.inbox_dir / row["file"]
        return {"cand_id": cand_id, "status": "fetched", "path": str(path), "format": "jats" if path.suffix == ".xml" else "pdf", "error": None}
    _update(conn, cand_id, status="fetching", error=None)
    say({"event": "candidate", "cand_id": cand_id, "status": "fetching"})
    tried: list[str] = []
    unreachable = False
    name = file_key(row)
    pmcid = row.get("pmcid")
    try:
        if pmcid and row.get("has_xml"):
            say({"event": "candidate", "cand_id": cand_id, "status": "fetching", "format": "jats"})
            try:
                xml = _get(f"{rest_base(base)}/{pmcid}/fullTextXML", timeout)
                if b"<article" in xml[:8000]:
                    dest = lib.inbox_dir / f"{name}.xml"
                    _save(dest, xml)
                    _update(conn, cand_id, status="fetched", file=dest.name, error=None)
                    say({"event": "candidate", "cand_id": cand_id, "status": "fetched", "format": "jats", "path": str(dest)})
                    return {"cand_id": cand_id, "status": "fetched", "path": str(dest), "format": "jats", "error": None}
                tried.append("full text XML: not an article")
            except urllib.error.HTTPError as e:
                tried.append(f"full text XML: HTTP {e.code}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                tried.append(f"full text XML: {e}")
                unreachable = True
        if pmcid and (row.get("is_open_access") or row.get("has_pdf")):
            say({"event": "candidate", "cand_id": cand_id, "status": "fetching", "format": "pdf"})
            try:
                body = _pdf_from(_get(pdf_url(pmcid, pdf), timeout), pmcid)
                if body is not None:
                    dest = lib.inbox_dir / f"{name}.pdf"
                    _save(dest, body)
                    _update(conn, cand_id, status="fetched", file=dest.name, error=None)
                    say({"event": "candidate", "cand_id": cand_id, "status": "fetched", "format": "pdf", "path": str(dest)})
                    return {"cand_id": cand_id, "status": "fetched", "path": str(dest), "format": "pdf", "error": None}
                tried.append("open-access PDF: no PDF in the answer")
            except urllib.error.HTTPError as e:
                tried.append(f"open-access PDF: HTTP {e.code}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                tried.append(f"open-access PDF: {e}")
                unreachable = True
    except Exception as e:  # noqa: BLE001 — one bad candidate never costs the rest
        _update(conn, cand_id, status="failed", error=f"{type(e).__name__}: {e}"[:400])
        say({"event": "candidate", "cand_id": cand_id, "status": "failed"})
        return {"cand_id": cand_id, "status": "failed", "path": None, "format": None, "error": f"{type(e).__name__}: {e}"}
    error = "; ".join(tried) or "no open full text"
    # A route that could not be reached says nothing about whether the paper is open: that is a
    # failure to try again, not a paper that wants a person to fetch it.
    status = "failed" if unreachable else "needs-pdf"
    _update(conn, cand_id, status=status, error=error[:400])
    out = {"cand_id": cand_id, "status": status, "path": None, "format": None, "error": error}
    if status == "needs-pdf":
        out["links"] = links(row)
    say({"event": "candidate", "cand_id": cand_id, "status": status})
    return out


def fetch(lib: Library, conn: sqlite3.Connection, cand_ids: Iterable[int] | None = None,
          on_progress: Callable[[dict[str, Any]], None] | None = None, *, timeout: float = 90,
          base: str | None = None, pdf: str | None = None) -> list[dict[str, Any]]:
    """Fetch candidates into the inbox, XML first, then PDF, else `needs-pdf`:
    `[{cand_id, status, path, format, error}]`. With no ids, every `staged` candidate. The worker
    files the returned paths with its `ingest`, then calls `reconcile`."""
    ensure_schema(conn)
    lib.inbox_dir.mkdir(parents=True, exist_ok=True)
    if cand_ids is None:
        cand_ids = [r[0] for r in conn.execute("SELECT cand_id FROM candidates WHERE status = 'staged' ORDER BY cand_id")]
    return [fetch_one(lib, conn, int(cid), timeout=timeout, base=base, pdf=pdf, on_progress=on_progress) for cid in cand_ids]


# ---------------------------------------------------------------- manual use


def _open(lib_dir: Path) -> tuple[Library, sqlite3.Connection]:
    from .store import open_store

    lib_dir = Path(lib_dir).resolve()
    manifest_path = lib_dir / "library.json"
    if not manifest_path.exists():
        raise SystemExit(f"not a library (no library.json): {lib_dir}")
    lib = Library(root=lib_dir.parent, id=lib_dir.name, manifest=json.loads(manifest_path.read_text("utf-8")))
    conn = open_store(lib.store_path)
    ensure_schema(conn)
    return lib, conn


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m litrag_parser.acquire", description="Search Europe PMC into a library's candidates, and fetch them.")
    ap.add_argument("--lib", required=True, help="the library's folder")
    ap.add_argument("--search", help="a Europe PMC query")
    ap.add_argument("--size", type=int, default=25)
    ap.add_argument("--cursor", default="*")
    ap.add_argument("--fetch", nargs="*", type=int, help="candidate ids to fetch (none: every staged one)")
    ap.add_argument("--stage", nargs="+", type=int)
    ap.add_argument("--dismiss", nargs="+", type=int)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status")
    ap.add_argument("--wanted", action="store_true")
    a = ap.parse_args(argv)
    lib, conn = _open(Path(a.lib))
    try:
        if a.search:
            page = search(a.search, page_size=a.size, cursor=a.cursor)
            out: Any = {**record_search(lib, conn, a.search, page["hits"], total=page["total"]), "next_cursor": page["next_cursor"]}
        elif a.stage:
            out = stage(conn, a.stage)
        elif a.dismiss:
            out = dismiss(conn, a.dismiss)
        elif a.fetch is not None:
            out = fetch(lib, conn, a.fetch or None)
        elif a.wanted:
            out = wanted(conn)
        else:
            out = candidates(conn, status=a.status)
    except AcquireError as e:
        print(json.dumps({"error": str(e)}))
        return 1
    finally:
        conn.close()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
