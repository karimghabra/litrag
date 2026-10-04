"""Finding papers and fetching them: Europe PMC's search, a table of candidates, XML first.

A search is asked of Europe PMC (`resultType=core`, a cursor for the next page) and every hit
becomes a row in `candidates`, in the library's own store, so a person can `SELECT` what was
found, what was fetched and what still wants a PDF. A candidate is found once: unique by DOI, by
PMID and by PMCID, so a search run twice adds nothing twice, and a candidate already seen keeps
its status (invariant 4).

Fetching goes the way the pairs taught: the JATS full text from the REST service first, because
it is the paper's own structure; else PMC's own XML from NCBI, by PMCID, which serves what the
REST service will not — an NIH author manuscript is in PMC but not in the open-access subset,
and `fullTextXML` answers 500 for it (2026-09-30: 23 of the pilot's 28 PDFs in PMC are these);
else the publisher's PDF: from NLM's PMC Cloud Service first
(`pmc-oa-opendata.s3.amazonaws.com`, the open-access subset as files, one folder per version of
an article, a JSON beside each naming its PDF and that PDF's MD5 — the successor NCBI named when
it retired its OA web service and FTP packages, 2026-08), then from EBI's bulk open-access area
(`ftp.ebi.ac.uk/pub/databases/pmc/pdf/OA/PMCxxxx<block>/<PMCID>.zip`, the same place the corpus
scripts fetch from, which misses many papers the Cloud Service holds — the websites' `?pdf=render`
links sit behind a bot check and are left alone); else the candidate `needs-pdf`, with the links
a person can follow to get it by hand and drop it into the app. With an XML, the same paper's PDF
is fetched too where either holds one: the XML names its figures but holds none, and the PDF is
kept beside it to read them (figures.py). What is fetched lands in the library's inbox; the worker's `ingest`
files it (DOI, then PMID, then hash) and `reconcile` marks the candidate `ingested`.

The only hosts asked are Europe PMC's (EBI's), NCBI's E-utilities and NLM's PMC Cloud Service,
and the last two are only ever sent a PMCID: an identifier out, the article in. Every base can be
pointed elsewhere, for tests and the end-to-end harness: `LITRAG_EPMC_URL` for the REST base,
`LITRAG_EPMC_PDF_URL` for the bulk PDF base, `LITRAG_NCBI_URL` for E-utilities
(`LITRAG_NCBI_EMAIL` and `LITRAG_NCBI_API_KEY`, when set, go with each NCBI request as its usage
policy asks; neither is ever filled in for you), `LITRAG_PMC_CLOUD_URL` for the Cloud Service.

    python -m litrag_parser.acquire --lib DIR --search "hydrogel cartilage" [--size 25]
    python -m litrag_parser.acquire --lib DIR --fetch 1 2 3
    python -m litrag_parser.acquire --lib DIR --list [--status needs-pdf] | --wanted
"""

from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import math
import os
import re
import sqlite3
import time
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
NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PMC_CLOUD = "https://pmc-oa-opendata.s3.amazonaws.com"
USER_AGENT = "litrag (local research tool; one request at a time)"
NCBI_GAP = 0.34  # E-utilities ask for at most three requests a second without an API key

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
  updated_at TEXT NOT NULL,
  round INTEGER,                    -- how the library reached it: 1 a search, n+1 the citations of a round-n paper (graph.py)
  published TEXT,                   -- Europe PMC's first publication date, YYYY-MM-DD
  author_list TEXT,                 -- JSON [{name, family, given, initials, orcid}], from the record's author list
  openalex TEXT,                    -- OpenAlex's id of the work (W…), when a round found it there (openalex.py)
  oa_url TEXT                       -- where OpenAlex says an open copy is, for a person to follow
);
CREATE UNIQUE INDEX IF NOT EXISTS candidates_doi ON candidates(doi) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS candidates_pmid ON candidates(pmid) WHERE pmid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS candidates_pmcid ON candidates(pmcid) WHERE pmcid IS NOT NULL;
CREATE INDEX IF NOT EXISTS candidates_status ON candidates(status);
"""
#: Made once the columns it needs exist (an older table gains them in `ensure_schema`).
INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS candidates_openalex ON candidates(openalex) WHERE openalex IS NOT NULL;
"""

#: The columns a hit fills, in the order they are inserted.
_FIELDS = ("pmid", "pmcid", "doi", "title", "authors", "journal", "year", "abstract", "pub_types", "cited_by", "is_open_access", "has_xml", "has_pdf",
           "published", "author_list", "openalex", "oa_url")
#: Columns added since the table was first made, for a library made before them.
_ADDED = (("round", "INTEGER"), ("published", "TEXT"), ("author_list", "TEXT"), ("openalex", "TEXT"), ("oa_url", "TEXT"))


class AcquireError(RuntimeError):
    """Europe PMC could not be asked, or answered with something that is not an answer."""


def rest_base(base: str | None = None) -> str:
    return (base or os.environ.get("LITRAG_EPMC_URL") or REST).rstrip("/")


def pdf_base(base: str | None = None) -> str:
    return (base or os.environ.get("LITRAG_EPMC_PDF_URL") or BULK_PDF).rstrip("/")


def ncbi_base(base: str | None = None) -> str:
    return (base or os.environ.get("LITRAG_NCBI_URL") or NCBI).rstrip("/")


def pmc_cloud_base(base: str | None = None) -> str:
    return (base or os.environ.get("LITRAG_PMC_CLOUD_URL") or PMC_CLOUD).rstrip("/")


def _get(url: str, timeout: float, retries: int = 3) -> bytes:
    """One GET; a busy service (429, 502, 503, 504) is asked again after a pause, since Europe
    PMC answers 503 to a burst of requests — measured on 76 DOI lookups, 11 of them — and a
    busy answer says nothing about the paper."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or attempt == retries:
                raise
            time.sleep(1.5 * 2 ** attempt)
    raise AssertionError("unreachable")


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
    for an XML full text anyone may fetch: a PMCID, and either in Europe PMC and open access
    (`fullTextXML`) or an author manuscript (NCBI's `efetch`). `has_pdf` is Europe PMC's own
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
    author_ms = _flag(h.get("authMan")) or _flag(h.get("nihAuthMan"))
    try:
        cited = int(h.get("citedByCount") or 0)
    except (TypeError, ValueError):
        cited = 0
    published = str(h.get("firstPublicationDate") or "").strip()
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
        "has_xml": bool(pmcid and ((in_epmc and oa) or author_ms)),
        "cited_by": cited,
        "pub_types": list(types),
        "published": published if re.fullmatch(r"\d{4}(-\d{2}){0,2}", published) else None,
        "author_list": author_list(h),
    }


def author_list(h: dict[str, Any]) -> list[dict[str, Any]] | None:
    """A `core` record's authors, each as the record splits them: `{name, family, given,
    initials, orcid}` (a consortium is a name alone). None when the record lists none — a
    `lite` record never does; its `authorString` is all there is."""
    out: list[dict[str, Any]] = []
    for a in ((h.get("authorList") or {}).get("author") or []):
        if not isinstance(a, dict):
            continue
        orcid = (a.get("authorId") or {}) if isinstance(a.get("authorId"), dict) else {}
        family = plain_text(a.get("lastName"))
        name = plain_text(a.get("fullName")) or plain_text(a.get("collectiveName")) or family
        if not name:
            continue
        out.append({"name": name, "family": family, "given": plain_text(a.get("firstName")), "initials": plain_text(a.get("initials")),
                    "orcid": str(orcid.get("value") or "").strip() or None if str(orcid.get("type") or "").upper() == "ORCID" else None})
    return out or None


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


# ---------------------------------------------------------------- citations


def _listing(kind: str, pmid: str | None, pmcid: str | None, timeout: float, base: str | None, most: int) -> list[dict[str, Any]] | None:
    """Europe PMC's list of a paper's references or citations, by PMID (`MED`), else PMCID
    (`PMC`), a thousand to a page: None when the paper has neither, or the service has no list."""
    if pmid:
        src, ident = "MED", str(pmid)
    elif pmcid:
        src, ident = "PMC", str(pmcid).upper()
    else:
        return None
    field, item = ("referenceList", "reference") if kind == "references" else ("citationList", "citation")
    out: list[dict[str, Any]] = []
    page = 1
    while len(out) < most:
        url = f"{rest_base(base)}/{src}/{ident}/{kind}?{urllib.parse.urlencode({'format': 'json', 'pageSize': 1000, 'page': page})}"
        try:
            data = json.loads(_get(url, timeout).decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        except json.JSONDecodeError as e:
            raise AcquireError(f"Europe PMC's {kind} of {src}/{ident} are not JSON") from e
        got = (data.get(field) or {}).get(item) or []
        out.extend(x for x in got if isinstance(x, dict))
        if len(got) < 1000 or len(out) >= int(data.get("hitCount") or 0):
            break
        page += 1
    return out[:most]


def references_of(pmid: str | None, pmcid: str | None = None, *, timeout: float = 30, base: str | None = None) -> list[dict[str, Any]] | None:
    """What a paper cites, as Europe PMC matched its reference list: each entry with `id` and
    `source` (`MED`: a PMID) where it found the cited paper, its title, authors and year as
    printed, `citedOrder` its place in the list."""
    return _listing("references", pmid, pmcid, timeout, base, 10000)


def citations_of(pmid: str | None, pmcid: str | None = None, *, timeout: float = 30, base: str | None = None, most: int = 1000) -> list[dict[str, Any]] | None:
    """What cites a paper, as Europe PMC knows it, newest first, at most `most`."""
    return _listing("citations", pmid, pmcid, timeout, base, most)


def ident_of(*, doi: Any = None, pmid: Any = None, pmcid: Any = None, openalex: Any = None) -> str | None:
    """A paper's identity for a lookup: `pmid:…`, else `doi:…` (lowercased), else `pmcid:…`, else
    `openalex:W…`."""
    if pmid and str(pmid).strip().isdigit():
        return f"pmid:{str(pmid).strip()}"
    if doi and str(doi).strip():
        return f"doi:{str(doi).strip().lower()}"
    if pmcid and re.fullmatch(r"(?i)pmc\d+", str(pmcid).strip()):
        return f"pmcid:{str(pmcid).strip().upper()}"
    if openalex and re.fullmatch(r"W\d+", str(openalex).strip()):
        return f"openalex:{str(openalex).strip()}"
    return None


_LOOKUP_BATCH = 20  # identifiers ORed into one query: well inside the service's query length


def lookup(idents: Iterable[str], *, timeout: float = 60, base: str | None = None,
           on_batch: Callable[[int, int], None] | None = None) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Europe PMC's `core` record of each identity (`pmid:…`, `doi:…`, `pmcid:…`), twenty to a
    query: `({ident: hit}, missed)` — a hit for each it knows, taken only for the identity it
    carries back (a PMID asked as `EXT_ID` names its source, `SRC:MED`: a bare number once
    fetched a different article, NOTES.md); `missed` the identities of a batch that could not be
    asked (unreachable, timed out twice, refused), which say nothing about the works."""
    idents = list(dict.fromkeys(i for i in idents if i))
    out: dict[str, dict[str, Any]] = {}
    missed: list[str] = []
    batches = [idents[i:i + _LOOKUP_BATCH] for i in range(0, len(idents), _LOOKUP_BATCH)]
    for n, batch in enumerate(batches):
        if on_batch:
            on_batch(n, len(batches))
        terms = []
        for ident in batch:
            kind, value = ident.split(":", 1)
            terms.append(f"(EXT_ID:{value} AND SRC:MED)" if kind == "pmid" else f'DOI:"{value}"' if kind == "doi" else f"PMCID:{value}")
        params = urllib.parse.urlencode({"query": " OR ".join(terms), "resultType": "core", "format": "json", "pageSize": 100})
        data = None
        for attempt in range(2):  # a busy answer is asked again by _get; a slow one once more here
            try:
                data = json.loads(_get(f"{rest_base(base)}/search?{params}", timeout).decode("utf-8"))
                break
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
                if isinstance(e, urllib.error.HTTPError) or attempt:
                    break
                time.sleep(2)
        if data is None:
            missed.extend(batch)
            continue
        wanted = set(batch)
        for h in (data.get("resultList") or {}).get("result") or []:
            hit = normalise_hit(h)
            for ident in (f"pmid:{hit['pmid']}" if hit["pmid"] else None, f"doi:{hit['doi']}" if hit["doi"] else None, f"pmcid:{hit['pmcid']}" if hit["pmcid"] else None):
                if ident in wanted and ident not in out:
                    out[ident] = hit
    return out, missed


# ---------------------------------------------------------------- the table


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(candidates)")}
    for col, kind in _ADDED:
        if col not in have:
            conn.execute(f"ALTER TABLE candidates ADD COLUMN {col} {kind}")
    conn.executescript(INDEXES)
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
    al = hit.get("author_list")
    v["author_list"] = (al if isinstance(al, str) else json.dumps(al, ensure_ascii=False)) if al else None  # a row's own JSON, or a record's list
    for k in ("is_open_access", "has_xml", "has_pdf"):
        v[k] = 1 if hit.get(k) else 0
    v["doi"] = (v["doi"] or "").lower() or None
    v["pmcid"] = (v["pmcid"] or "").upper() or None
    return v


def _existing(conn: sqlite3.Connection, v: dict[str, Any]) -> int | None:
    for col in ("doi", "pmid", "pmcid", "openalex"):
        if v.get(col):
            r = conn.execute(f"SELECT cand_id FROM candidates WHERE {col} = ?", (v[col],)).fetchone()
            if r is not None:
                return int(r[0])
    if not (v.get("doi") or v.get("pmid") or v.get("pmcid") or v.get("openalex")) and v.get("title"):
        r = conn.execute("SELECT cand_id FROM candidates WHERE doi IS NULL AND pmid IS NULL AND pmcid IS NULL AND openalex IS NULL AND title = ? AND COALESCE(year, '') = ?", (v["title"], v.get("year") or "")).fetchone()
        if r is not None:
            return int(r[0])
    return None


def upsert_candidate(conn: sqlite3.Connection, hit: dict[str, Any], *, query: str | None, now: str, status: str = "found", round: int = 1) -> tuple[int, bool]:
    """A hit as a candidate, once. Returns `(cand_id, added)`. A candidate already seen keeps its
    status and its query; what it lacked is filled, and nothing it has is replaced — but for its
    round, which is the earliest that reached it."""
    v = _values(hit)
    cid = _existing(conn, v)
    if cid is None:
        cols = ("query", *_FIELDS, "status", "found_at", "updated_at", "round")
        cur = conn.execute(f"INSERT INTO candidates({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", (query, *(v[k] for k in _FIELDS), status, now, now, round))
        return int(cur.lastrowid), True
    conn.execute("UPDATE candidates SET round = ? WHERE cand_id = ? AND round > ?", (round, cid, round))  # NULL, from before rounds, is a search's: 1
    fill_candidate(conn, cid, hit)
    return cid, False


def fill_candidate(conn: sqlite3.Connection, cid: int, hit: dict[str, Any]) -> None:
    """What a candidate lacks, from another record of the same work; nothing it has is replaced,
    and an identifier another candidate already holds stays that one's — two partial rows of one
    paper stay two rather than one being overwritten by the other."""
    v = _values(hit)
    for col in _FIELDS:
        if v.get(col) is None:
            continue
        if col in ("doi", "pmid", "pmcid", "openalex"):
            if conn.execute(f"SELECT 1 FROM candidates WHERE {col} = ? AND cand_id != ?", (v[col], cid)).fetchone():
                continue
        conn.execute(f"UPDATE candidates SET {col} = COALESCE({col}, ?) WHERE cand_id = ?", (v[col], cid))


def _write_manifest(lib: Library, update: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Read library.json fresh, change it, write it the way `create_library` does."""
    from .library import update_manifest

    return update_manifest(lib, update)  # one writer at a time: a search, a description and a merge share it


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
    if row.get("oa_url"):
        out["open"] = str(row["oa_url"])  # an open copy OpenAlex knows of, outside PMC: for a person to follow, never fetched
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
    """The candidates that want a PDF from a person: nothing open was there to fetch. Most-cited
    first, as `lit wanted` lists them, since the collect window walks them in this order."""
    return sorted(candidates(conn, status="needs-pdf"), key=lambda c: (-(c.get("cited_by") or 0), c["cand_id"]))


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


def ncbi_url(pmcid: str, base: str | None = None) -> str:
    """PMC's own XML for one PMCID, from E-utilities. Only the identifier goes out; the contact
    fields NCBI asks for are sent only when the person has set them."""
    params = {"db": "pmc", "id": pmcid.upper().removeprefix("PMC"), "tool": "litrag"}
    for env, key in (("LITRAG_NCBI_EMAIL", "email"), ("LITRAG_NCBI_API_KEY", "api_key")):
        if os.environ.get(env):
            params[key] = os.environ[env]
    return f"{ncbi_base(base)}/efetch.fcgi?{urllib.parse.urlencode(params)}"


_ncbi_last = 0.0


def _ncbi_get(url: str, timeout: float) -> bytes:
    """One E-utilities GET, never closer than `NCBI_GAP` to the last."""
    global _ncbi_last
    wait = _ncbi_last + NCBI_GAP - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        return _get(url, timeout)
    finally:
        _ncbi_last = time.monotonic()


_ARTICLE = re.compile(rb"<article[\s>]")
_BODY = re.compile(rb"<body[\s>]")


def _article_from(data: bytes) -> bytes | None:
    """The article out of `efetch`'s `<pmc-articleset>`, as Europe PMC serves one (a declaration,
    then `<article>`, its bytes as NCBI sent them), when it carries a body. An article NCBI may
    not give out comes back as an error, or as its front matter alone: neither is full text."""
    start = _ARTICLE.search(data)
    end = data.rfind(b"</article>")
    if start is None or end < start.start():
        return None
    article = data[start.start():end + len(b"</article>")]
    if not _BODY.search(article):
        return None
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + article


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


_VERSION = r"<Prefix>{}\.(\d+)/</Prefix>"
_S3 = re.compile(r"^s3://[^/]+/([^?#]+)(?:\?md5=([0-9a-fA-F]{32}))?")


def pmc_cloud_pdf(pmcid: str, timeout: float, base: str | None = None) -> tuple[bytes | None, str]:
    """A paper's PDF from the PMC Cloud Service, by PMCID: `(pdf, "")`, or `(None, why not)`.
    The bucket lists an article's versions as folders (`PMC11278924.1/`); the latest one's JSON
    names its PDF (`pdf_url`, an `s3://` address carrying the PDF's MD5), and the PDF is taken
    only if it is one and its MD5 holds. An author manuscript is there as XML and text, with no
    PDF; a paper outside the open-access datasets is not there at all."""
    pmcid = pmcid.upper()
    b = pmc_cloud_base(base)
    listing = _get(f"{b}/?{urllib.parse.urlencode({'list-type': 2, 'prefix': f'{pmcid}.', 'delimiter': '/'})}", timeout)
    versions = [int(v) for v in re.findall(_VERSION.format(re.escape(pmcid)), listing.decode("utf-8", "replace"))]
    if not versions:
        return None, "not in its open-access datasets"
    v = max(versions)
    try:
        meta = json.loads(_get(f"{b}/{pmcid}.{v}/{pmcid}.{v}.json", timeout))
    except ValueError:
        return None, "its record would not read"
    m = _S3.match(str(meta.get("pdf_url") or ""))
    if m is None:
        return None, "no PDF (an author manuscript)" if meta.get("is_manuscript") else "no PDF"
    body = _get(f"{b}/{m.group(1)}", timeout)
    if body[:4] != b"%PDF":
        return None, "not a PDF in the answer"
    if m.group(2) and hashlib.md5(body).hexdigest() != m.group(2).lower():
        return None, "the PDF failed its MD5"
    return body, ""


def _save(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def _update(conn: sqlite3.Connection, cid: int, **cols: Any) -> None:
    cols["updated_at"] = now_iso()
    with conn:
        conn.execute(f"UPDATE candidates SET {', '.join(f'{k} = ?' for k in cols)} WHERE cand_id = ?", (*cols.values(), cid))


def _figures_pdf(lib: Library, pmcid: str | None, name: str, pdf: str | None, cloud: str | None, timeout: float) -> str | None:
    """After the XML, the same paper's PDF, for its figures: the XML names its figures but holds
    none, and a PDF draws them (figures.py). The PMC Cloud Service first, then the bulk
    open-access area, the hosts the PDF route asks; only open PDFs are filed there, so a miss is
    no failure and says nothing of the XML. Off with `LITRAG_FIGURES=off`."""
    from .figures import enabled

    if not pmcid or not enabled():
        return None
    body = None
    try:
        body, _ = pmc_cloud_pdf(pmcid, timeout, cloud)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        pass
    if body is None:
        try:
            body = _pdf_from(_get(pdf_url(pmcid, pdf), timeout), pmcid)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            return None
    if body is None:
        return None
    dest = lib.inbox_dir / f"{name}.pdf"
    _save(dest, body)
    return str(dest)


def fetch_one(lib: Library, conn: sqlite3.Connection, cand_id: int, *, timeout: float = 90, base: str | None = None,
              pdf: str | None = None, ncbi: str | None = None, cloud: str | None = None,
              on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """One candidate: its JATS from Europe PMC, else PMC's XML from NCBI, else its PDF from the
    PMC Cloud Service or the bulk area, else `needs-pdf`. With an XML, its PDF as well where
    there is one, for its figures (`figures` in the answer: the PDF's path, or None). Never raises for the candidate; what went
    wrong is the answer's `error` and the row's."""
    say = on_progress or (lambda e: None)
    row = _row(conn, cand_id)
    if row is None:
        return {"cand_id": cand_id, "status": None, "path": None, "format": None, "error": "no such candidate"}
    if row["status"] in ("ingested", "dismissed"):
        return {"cand_id": cand_id, "status": row["status"], "path": None, "format": None, "error": None}
    if row["status"] == "fetched" and row["file"] and (lib.inbox_dir / row["file"]).exists():
        # fetched before and still waiting in the inbox: the same file, not a second download
        path = lib.inbox_dir / row["file"]
        beside = path.with_suffix(".pdf") if path.suffix == ".xml" else None
        return {"cand_id": cand_id, "status": "fetched", "path": str(path), "format": "jats" if path.suffix == ".xml" else "pdf", "error": None,
                "figures": str(beside) if beside is not None and beside.exists() else None}
    _update(conn, cand_id, status="fetching", error=None)
    say({"event": "candidate", "cand_id": cand_id, "status": "fetching"})
    tried: list[str] = []
    unreachable = False
    name = file_key(row)
    pmcid = row.get("pmcid")
    try:
        if pmcid and row.get("has_xml") and row.get("is_open_access"):
            say({"event": "candidate", "cand_id": cand_id, "status": "fetching", "format": "jats", "source": "europepmc"})
            try:
                xml = _get(f"{rest_base(base)}/{pmcid}/fullTextXML", timeout)
                if b"<article" in xml[:8000]:
                    dest = lib.inbox_dir / f"{name}.xml"
                    _save(dest, xml)
                    _update(conn, cand_id, status="fetched", file=dest.name, error=None)
                    say({"event": "candidate", "cand_id": cand_id, "status": "fetched", "format": "jats", "source": "europepmc", "path": str(dest)})
                    return {"cand_id": cand_id, "status": "fetched", "path": str(dest), "format": "jats", "source": "europepmc", "error": None,
                            "figures": _figures_pdf(lib, pmcid, name, pdf, cloud, timeout)}
                tried.append("full text XML: not an article")
            except urllib.error.HTTPError as e:
                tried.append(f"full text XML: HTTP {e.code}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                tried.append(f"full text XML: {e}")
                unreachable = True
        if pmcid:
            # Asked of every PMCID the REST service did not serve, not only the ones flagged as
            # author manuscripts: a candidate found before this route existed has `has_xml` 0
            # on file, and a search run again never replaces what a row already has.
            say({"event": "candidate", "cand_id": cand_id, "status": "fetching", "format": "jats", "source": "ncbi"})
            try:
                article = _article_from(_ncbi_get(ncbi_url(pmcid, ncbi), timeout))
                if article is not None:
                    dest = lib.inbox_dir / f"{name}.xml"
                    _save(dest, article)
                    _update(conn, cand_id, status="fetched", file=dest.name, error=None)
                    say({"event": "candidate", "cand_id": cand_id, "status": "fetched", "format": "jats", "source": "ncbi", "path": str(dest)})
                    return {"cand_id": cand_id, "status": "fetched", "path": str(dest), "format": "jats", "source": "ncbi", "error": None,
                            "figures": _figures_pdf(lib, pmcid, name, pdf, cloud, timeout)}
                tried.append("NCBI PMC XML: no full text in the answer")
            except urllib.error.HTTPError as e:
                tried.append(f"NCBI PMC XML: HTTP {e.code}")  # 400: PMC holds it, NCBI may not give it out
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                tried.append(f"NCBI PMC XML: {e}")
                unreachable = True
        if pmcid:
            # Asked of every PMCID, like NCBI: its listing says at once whether it holds the paper,
            # and Europe PMC's flags are not the open-access subset's.
            say({"event": "candidate", "cand_id": cand_id, "status": "fetching", "format": "pdf", "source": "pmc-cloud"})
            try:
                body, why = pmc_cloud_pdf(pmcid, timeout, cloud)
                if body is not None:
                    dest = lib.inbox_dir / f"{name}.pdf"
                    _save(dest, body)
                    _update(conn, cand_id, status="fetched", file=dest.name, error=None)
                    say({"event": "candidate", "cand_id": cand_id, "status": "fetched", "format": "pdf", "source": "pmc-cloud", "path": str(dest)})
                    return {"cand_id": cand_id, "status": "fetched", "path": str(dest), "format": "pdf", "source": "pmc-cloud", "error": None}
                tried.append(f"PMC Cloud: {why}")
            except urllib.error.HTTPError as e:
                tried.append(f"PMC Cloud: HTTP {e.code}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                tried.append(f"PMC Cloud: {e}")
                unreachable = True
        if pmcid and (row.get("is_open_access") or row.get("has_pdf")):
            say({"event": "candidate", "cand_id": cand_id, "status": "fetching", "format": "pdf", "source": "europepmc"})
            try:
                body = _pdf_from(_get(pdf_url(pmcid, pdf), timeout), pmcid)
                if body is not None:
                    dest = lib.inbox_dir / f"{name}.pdf"
                    _save(dest, body)
                    _update(conn, cand_id, status="fetched", file=dest.name, error=None)
                    say({"event": "candidate", "cand_id": cand_id, "status": "fetched", "format": "pdf", "source": "europepmc", "path": str(dest)})
                    return {"cand_id": cand_id, "status": "fetched", "path": str(dest), "format": "pdf", "source": "europepmc", "error": None}
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
          base: str | None = None, pdf: str | None = None, ncbi: str | None = None, cloud: str | None = None) -> list[dict[str, Any]]:
    """Fetch candidates into the inbox, XML first (Europe PMC's, then NCBI's), then PDF (the PMC
    Cloud Service's, then the bulk area's), else
    `needs-pdf`: `[{cand_id, status, path, format, source, error}]`. With no ids, every `staged`
    candidate. The worker files the returned paths with its `ingest`, then calls `reconcile`."""
    ensure_schema(conn)
    lib.inbox_dir.mkdir(parents=True, exist_ok=True)
    if cand_ids is None:
        cand_ids = [r[0] for r in conn.execute("SELECT cand_id FROM candidates WHERE status = 'staged' ORDER BY cand_id")]
    return [fetch_one(lib, conn, int(cid), timeout=timeout, base=base, pdf=pdf, ncbi=ncbi, cloud=cloud, on_progress=on_progress) for cid in cand_ids]


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
