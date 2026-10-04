"""The library as a graph of works: which paper cites which, who wrote what, and the rounds by
which the library grew — all of it rows a person can `SELECT`.

A *work* is a paper the library holds (its `papers.key`) or a candidate it does not hold yet
(`cand:<id>`, acquire.py). Three things are kept beside the papers and the candidates:

- `cites`: one row per citation between two works, `citing` → `cited`. It comes from a held
  paper's own reference list as read here (`origin` 'refs', with the entry's number), or from
  Europe PMC's lists of a paper's references and of what cites it (`origin` 'europepmc'). An
  entry that names neither a held paper nor an identifier is no row: it stays in `refs`,
  unresolved, never guessed onto a paper (invariant 5).
- `authors`: one row per author of every work, in order, with the family name and initials,
  the ORCID where the record gives one, and `person` — family name and first initial, folded —
  which joins one person across works ("akkus o").
- `works`: a view over papers and candidates alike — identifiers, title, year, first
  publication date, journal, `state` (held or candidate), `round`, first author, and how many
  works here cite it (`cited_here`) and it cites (`cites_here`).

A *round* is how the library grows. Round 1 is what a search found or a person dropped in; a
citation round asks, for held papers, what they cite (and, when asked, what cites them), and
files every work it can identify as a candidate of the next round: found, not fetched. Fetching
stays the person's choice, so any `SELECT` over `works` — chronological, by an author, the most
cited here and not held — is a way to choose what the library reads next. The hosts asked are
Europe PMC's and OpenAlex's (openalex.py), side by side, sent identifiers — and OpenAlex, for an
entry that names none, the entry's own words.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from typing import Any, Callable, Iterable

from . import acquire
from .library import Library, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS cites (
  citing TEXT NOT NULL,    -- a work: a papers.key, or 'cand:<id>'
  cited TEXT NOT NULL,     -- a work, the same way
  origin TEXT NOT NULL,    -- refs: the citing paper's reference list as read here | europepmc: Europe PMC's lists
  ref_no INTEGER,          -- the entry in the citing paper's list, when read from it
  PRIMARY KEY (citing, cited, origin)
);
CREATE INDEX IF NOT EXISTS cites_cited ON cites(cited);
CREATE TABLE IF NOT EXISTS authors (
  work TEXT NOT NULL,      -- a papers.key, or 'cand:<id>'
  pos INTEGER NOT NULL,    -- 1-based, in the order printed
  name TEXT NOT NULL,      -- as printed: "Ozan Akkus", "Akkus O"
  family TEXT,
  initials TEXT,
  orcid TEXT,
  person TEXT,             -- family name and first initial, lowercased, accents dropped: "akkus o"
  PRIMARY KEY (work, pos)
);
CREATE INDEX IF NOT EXISTS authors_person ON authors(person);
CREATE INDEX IF NOT EXISTS candidates_paper ON candidates(paper_key);
CREATE TABLE IF NOT EXISTS graph_state (name TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS harvests (
  paper TEXT NOT NULL,     -- the held paper asked about
  kind TEXT NOT NULL,      -- references | citations | local (its list linked to the papers held, as of `at`: its reading's time)
  at TEXT NOT NULL,
  found INTEGER,           -- the works it named that could be identified
  PRIMARY KEY (paper, kind)
);
"""

#: Rebuilt on every `ensure_schema`, so a change to it reaches libraries made before.
WORKS_VIEW = """
CREATE VIEW works AS
SELECT p.key AS work, p.key AS paper,
       (SELECT MIN(c.cand_id) FROM candidates c WHERE c.paper_key = p.key) AS cand_id,
       p.doi, p.pmid, p.pmcid, p.title,
       CAST(p.year AS INTEGER) AS year,
       (SELECT c.published FROM candidates c WHERE c.paper_key = p.key AND c.published IS NOT NULL ORDER BY c.cand_id LIMIT 1) AS published,
       p.journal, 'held' AS state, p.status,
       COALESCE((SELECT MIN(COALESCE(c.round, 1)) FROM candidates c WHERE c.paper_key = p.key), 1) AS round,
       (SELECT a.name FROM authors a WHERE a.work = p.key AND a.pos = 1) AS first_author,
       (SELECT COUNT(DISTINCT x.citing) FROM cites x WHERE x.cited = p.key) AS cited_here,
       (SELECT COUNT(DISTINCT x.cited) FROM cites x WHERE x.citing = p.key) AS cites_here,
       (SELECT MAX(c.cited_by) FROM candidates c WHERE c.paper_key = p.key) AS cited_by,
       p.type
FROM papers p
UNION ALL
SELECT 'cand:' || c.cand_id, NULL, c.cand_id, c.doi, c.pmid, c.pmcid, c.title,
       CAST(c.year AS INTEGER), c.published, c.journal, 'candidate', c.status, COALESCE(c.round, 1),
       (SELECT a.name FROM authors a WHERE a.work = 'cand:' || c.cand_id AND a.pos = 1),
       (SELECT COUNT(DISTINCT x.citing) FROM cites x WHERE x.cited = 'cand:' || c.cand_id),
       (SELECT COUNT(DISTINCT x.cited) FROM cites x WHERE x.citing = 'cand:' || c.cand_id),
       c.cited_by, NULL
FROM candidates c
WHERE c.paper_key IS NULL OR c.paper_key NOT IN (SELECT key FROM papers)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    acquire.ensure_schema(conn)
    conn.executescript(SCHEMA)
    have = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'view' AND name = 'works'").fetchone()
    if have is None or " ".join(str(have[0]).split()) != " ".join(WORKS_VIEW.split()):
        conn.execute("DROP VIEW IF EXISTS works")
        conn.execute(WORKS_VIEW.strip())
    conn.commit()


# ---------------------------------------------------------------- names


_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "dos", "du", "la", "le", "ter", "ten", "bin", "al", "el", "st"}
_INITIALS = re.compile(r"^(?:[A-Z]\.?-?){1,4}$")


def _fold(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch)).lower().strip()


def split_name(name: str) -> tuple[str | None, str | None]:
    """`(family, initials)` of a name as papers print it: "Akkus O" and "van Dillen T" (Europe
    PMC's way), "Ozan Akkus" and "Jan van der Berg" (given names first), "Akkus, Ozan". A single
    word is a family name with no initials; nothing is invented."""
    name = " ".join(str(name or "").split()).strip(" .,;")
    if not name:
        return None, None
    if "," in name:
        family, given = (x.strip() for x in name.split(",", 1))
        return family or None, _initials_of(given)
    words = name.split()
    if len(words) == 1:
        return words[0], None
    if _INITIALS.match(words[-1]) and not _INITIALS.match(words[0]):
        return " ".join(words[:-1]), re.sub(r"[^A-Z]", "", words[-1]) or None
    # given names first, the family name last, with the particles that belong to it
    i = len(words) - 1
    while i > 1 and words[i - 1].lower() in _PARTICLES:
        i -= 1
    return " ".join(words[i:]), _initials_of(" ".join(words[:i]))


def _initials_of(given: str) -> str | None:
    """"Ozan" → O, "Jean-Luc" → JL, "J.R." → JR."""
    letters = []
    for w in re.split(r"[\s\-]+", given or ""):
        if _INITIALS.match(w):
            letters.extend(ch for ch in w if ch.isupper())
        elif w and w[0].isalpha():
            letters.append(w[0].upper())
    return "".join(letters) or None


def person_key(family: str | None, initials: str | None) -> str | None:
    """One person across works: the family name folded, and the first initial."""
    if not family:
        return None
    return f"{_fold(family)} {_fold(initials[0])}" if initials else _fold(family)


def _author_rows(work: str, listed: Any, string: str | None) -> list[tuple[Any, ...]]:
    """A work's author rows from its JSON list (`[{name, family?, initials?, orcid?}]`), else from
    an author string ("Islam A, Younesi M")."""
    people: list[dict[str, Any]] = []
    if isinstance(listed, str):
        try:
            listed = json.loads(listed)
        except ValueError:
            listed = None
    if isinstance(listed, list):
        people = [a for a in listed if isinstance(a, dict) and a.get("name")]
    if not people and string:
        people = [{"name": a.strip().rstrip(".")} for a in str(string).split(",") if a.strip().rstrip(".") and a.strip() != "et al"]
    rows = []
    for pos, a in enumerate(people, start=1):
        family, initials = a.get("family"), a.get("initials")
        if not family:
            family, initials2 = split_name(a["name"])
            initials = initials or initials2
        elif not initials and a.get("given"):
            initials = _initials_of(a["given"])
        rows.append((work, pos, str(a["name"]), family, initials, a.get("orcid") or None, person_key(family, initials)))
    return rows


# ---------------------------------------------------------------- keeping the rows true


def sync(conn: sqlite3.Connection) -> dict[str, int]:
    """Bring the derived rows up to what papers and candidates say: a candidate that became a
    held paper is that paper in `cites` (its rows moved to the paper's key); every held paper's
    authors from its own record; a candidate's from its record; the citations among the papers
    held. Each part is done again only when what it reads has changed, so a `SELECT` that calls
    this first costs a few aggregate queries. Running it twice changes nothing."""
    ensure_schema(conn)
    moved = 0
    with conn:
        held = conn.execute(
            "SELECT c.cand_id, c.paper_key FROM candidates c WHERE c.paper_key IN (SELECT key FROM papers) AND ("
            " EXISTS (SELECT 1 FROM cites WHERE citing = 'cand:' || c.cand_id) OR EXISTS (SELECT 1 FROM cites WHERE cited = 'cand:' || c.cand_id)"
            " OR EXISTS (SELECT 1 FROM authors WHERE work = 'cand:' || c.cand_id))").fetchall()
        for cid, key in held:
            work = f"cand:{cid}"
            for col in ("citing", "cited"):
                moved += conn.execute(f"UPDATE OR IGNORE cites SET {col} = ? WHERE {col} = ?", (key, work)).rowcount
                conn.execute(f"DELETE FROM cites WHERE {col} = ?", (work,))  # what the paper already had
            conn.execute("DELETE FROM authors WHERE work = ?", (work,))
        if held:
            conn.execute("DELETE FROM cites WHERE citing = cited")
    authors = _sync_authors(conn)
    linked = link_held(conn)
    return {"moved": moved, "authors": authors, "linked": linked}


def _stamp(conn: sqlite3.Connection, name: str, sql: str) -> tuple[bool, str]:
    """Whether what `sql` measures changed since `name` was last done, and its measure now."""
    now = json.dumps([list(r) for r in conn.execute(sql)])
    kept = conn.execute("SELECT value FROM graph_state WHERE name = ?", (name,)).fetchone()
    return kept is None or kept[0] != now, now


def _sync_authors(conn: sqlite3.Connection) -> int:
    changed, stamp = _stamp(conn, "authors",
                            "SELECT COUNT(*), MAX(rowid), TOTAL(length(authors)) FROM papers"
                            " UNION ALL SELECT COUNT(*), MAX(cand_id), TOTAL(length(author_list)) + TOTAL(length(authors)) + COUNT(paper_key) FROM candidates")
    if not changed:
        return 0
    with conn:
        rows: list[tuple[Any, ...]] = []
        for key, listed in conn.execute("SELECT key, authors FROM papers").fetchall():
            got = _author_rows(key, listed, None)
            if not got:  # a paper with no record of its own: its candidate's
                c = conn.execute("SELECT author_list, authors FROM candidates WHERE paper_key = ? ORDER BY cand_id LIMIT 1", (key,)).fetchone()
                if c is not None:
                    got = _author_rows(key, c[0], c[1])
            rows.extend(got)
        conn.execute("DELETE FROM authors WHERE work IN (SELECT key FROM papers)")
        conn.executemany("INSERT OR REPLACE INTO authors VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        missing = conn.execute(
            "SELECT cand_id, author_list, authors FROM candidates c WHERE (paper_key IS NULL OR paper_key NOT IN (SELECT key FROM papers))"
            " AND NOT EXISTS (SELECT 1 FROM authors a WHERE a.work = 'cand:' || c.cand_id)").fetchall()
        for cid, listed, string in missing:
            rows.extend(got := _author_rows(f"cand:{cid}", listed, string))
            conn.executemany("INSERT OR REPLACE INTO authors VALUES (?, ?, ?, ?, ?, ?, ?)", got)
        conn.execute("INSERT OR REPLACE INTO graph_state VALUES ('authors', ?)", (stamp,))
    return len(rows)


def link_held(conn: sqlite3.Connection) -> int:
    """Every reference entry that names a paper the library holds, a `cites` row to it — no
    network, no candidate: the citations among the papers held, there as soon as both are read.
    Done again only when the papers or their readings change; a paper read again loses the rows
    its old list made (and is due another round)."""
    from .lineage import _Library

    changed, stamp = _stamp(conn, "linked",
                            "SELECT COUNT(*), MAX(rowid), GROUP_CONCAT(parsed_at), TOTAL(length(doi)), TOTAL(length(pmid)), TOTAL(length(title)) FROM papers"
                            " UNION ALL SELECT COUNT(*), MAX(rowid), NULL, NULL, NULL, NULL FROM refs")
    if not changed:
        return 0
    papers = [dict(r) for r in conn.execute("SELECT key, parsed_at FROM papers")]
    seen = {p: at for p, at in conn.execute("SELECT paper, at FROM harvests WHERE kind = 'local'")}
    resolver = _Library(conn)
    n = 0
    with conn:
        for p in papers:
            if p["key"] in seen and seen[p["key"]] != (p["parsed_at"] or ""):
                # read again: its old list's rows go, and its references are due another round
                conn.execute("DELETE FROM cites WHERE citing = ? AND origin = 'refs'", (p["key"],))
                conn.execute("DELETE FROM harvests WHERE paper = ? AND kind = 'references'", (p["key"],))
            for ref in conn.execute("SELECT ref_no, text, doi, pmid, year, first_author, title FROM refs WHERE paper = ?", (p["key"],)).fetchall():
                hit, _how = resolver.resolve(dict(ref), p["key"])
                if hit is not None:
                    n += conn.execute("INSERT OR IGNORE INTO cites VALUES (?, ?, 'refs', ?)", (p["key"], hit["key"], ref["ref_no"])).rowcount
            conn.execute("INSERT OR REPLACE INTO harvests VALUES (?, 'local', ?, NULL)", (p["key"], p["parsed_at"] or ""))
        conn.execute("INSERT OR REPLACE INTO graph_state VALUES ('linked', ?)", (stamp,))
    return n


def round_of(conn: sqlite3.Connection, key: str) -> int:
    """A held paper's round: its candidate's, or 1 for a paper dropped in."""
    r = conn.execute("SELECT MIN(COALESCE(round, 1)) FROM candidates WHERE paper_key = ?", (key,)).fetchone()
    return int(r[0]) if r and r[0] is not None else 1


# ---------------------------------------------------------------- a citation round


def _work_of(conn: sqlite3.Connection, cand_id: int) -> str:
    r = conn.execute("SELECT paper_key FROM candidates WHERE cand_id = ? AND paper_key IN (SELECT key FROM papers)", (cand_id,)).fetchone()
    return r[0] if r else f"cand:{cand_id}"


def _held_index(conn: sqlite3.Connection) -> dict[str, str]:
    """Every held paper by each identity it has: `{ident: key}`."""
    out: dict[str, str] = {}
    for key, doi, pmid, pmcid in conn.execute("SELECT key, doi, pmid, pmcid FROM papers"):
        for ident in (acquire.ident_of(pmid=pmid), acquire.ident_of(doi=doi), acquire.ident_of(pmcid=pmcid)):
            if ident:
                out.setdefault(ident, key)
    return out


def _cand_index(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for cid, doi, pmid, pmcid, oa_id in conn.execute("SELECT cand_id, doi, pmid, pmcid, openalex FROM candidates ORDER BY cand_id"):
        for ident in (acquire.ident_of(pmid=pmid), acquire.ident_of(doi=doi), acquire.ident_of(pmcid=pmcid), acquire.ident_of(openalex=oa_id)):
            if ident:
                out.setdefault(ident, cid)
    return out


def _epmc_ident(x: dict[str, Any]) -> str | None:
    src, ident = str(x.get("source") or "").upper(), str(x.get("id") or "").strip()
    if src == "MED" and ident.isdigit():
        return f"pmid:{ident}"
    if src == "PMC" and ident:
        return acquire.ident_of(pmcid=ident if ident.upper().startswith("PMC") else f"PMC{ident}")
    return acquire.ident_of(doi=x.get("doi"))


def _own_hit(ident: str, seen: dict[str, Any]) -> dict[str, Any]:
    """A work Europe PMC does not know, as the citing paper printed it: its identifier, title,
    first author and year — enough to list it and to fetch it by hand."""
    kind, value = ident.split(":", 1)
    return {"doi": value if kind == "doi" else seen.get("doi"), "pmid": value if kind == "pmid" else None, "pmcid": value if kind == "pmcid" else None,
            "openalex": value if kind == "openalex" else None,
            "title": seen.get("title"), "authors": seen.get("authors"), "journal": seen.get("journal"), "year": seen.get("year"),
            "has_xml": False, "is_open_access": False, "has_pdf": False, "cited_by": None, "pub_types": []}


#: A title this long, the same year and the same first author: one work under two identifiers (a
#: publisher that changed a paper's DOI, OpenAlex holding the other one).
SAME_TITLE_WORDS = 6


def _same_work(conn: sqlite3.Connection, h: dict[str, Any]) -> int | None:
    """The one candidate that is this record's work by everything but an identifier: its whole
    title (six words or more, letters and digits only), its year and its first author's family
    name. None when none is, or more than one."""
    title = " ".join(re.sub(r"[^a-z0-9]+", " ", str(h.get("title") or "").lower()).split())
    first = (h.get("author_list") or [{}])[0].get("name") or ""
    family = split_name(first)[0] if first else None
    if len(title.split()) < SAME_TITLE_WORDS or not h.get("year") or not family:
        return None
    same = []
    for cid, t, authors, listed in conn.execute("SELECT cand_id, title, authors, author_list FROM candidates WHERE year = ?", (str(h["year"]),)).fetchall():
        if " ".join(re.sub(r"[^a-z0-9]+", " ", str(t or "").lower()).split()) != title:
            continue
        rows = _author_rows("", listed, authors)  # (work, pos, name, family, …)
        if rows and _fold(rows[0][3] or "") == _fold(family):
            same.append(cid)
    return same[0] if len(same) == 1 else None


def _paper_ident(p: dict[str, Any]) -> str | None:
    return acquire.ident_of(doi=p["doi"]) or acquire.ident_of(pmid=p["pmid"]) or acquire.ident_of(pmcid=p["pmcid"])


def _fill_record(conn: sqlite3.Connection, key: str, w: dict[str, Any]) -> None:
    """A held paper's authors, journal and year from OpenAlex, where it has none of its own (a
    paper outside Europe PMC, read from a PDF): filled, never replacing what the file or Europe
    PMC said."""
    from . import openalex as oa
    from .store import set_record

    h = oa.hit(w)
    authors = [{"name": a["name"], "affiliations": [], "corresponding": False, **({"orcid": a["orcid"]} if a.get("orcid") else {})} for a in h["author_list"] or []]
    set_record(conn, key, authors=authors or None, journal=h["journal"], year=h["year"])


def harvest(lib: Library, conn: sqlite3.Connection, keys: Iterable[str] | None = None, *, references: bool = True, citations: bool = False,
            again: bool = False, network: bool = True, openalex: bool | None = None, most_citing: int = 1000,
            title_searches: int | None = None, timeout: float = 30,
            on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """One citation round from held papers (`keys`, else every held paper not asked before):
    what each cites and, with `citations`, what cites it — asked of Europe PMC and of OpenAlex
    side by side (`openalex=False` leaves OpenAlex out; `LITRAG_OPENALEX=off` always does), the paper's own reference list
    read beside them. A cited or citing work the library holds is a `cites` row to it; one with
    an identifier is a candidate of the paper's round + 1 and a `cites` row to that — filed with
    Europe PMC's record where it has one (the PMCID and open-access flags a fetch needs), else
    OpenAlex's, else what the reference printed. For a paper neither source has a list for, an
    entry naming no identifier is searched in OpenAlex by its words (at most `title_searches` a
    round) and taken only when one work's whole title, year and first author are in it; else it
    is counted and left. Nothing is fetched. Twice changes nothing; `again` asks once more."""
    from . import openalex as oa
    from .lineage import _Library

    ensure_schema(conn)
    sync(conn)
    say = on_progress or (lambda e: None)
    use_oa = network and oa.enabled() and (openalex is None or bool(openalex))  # `LITRAG_OPENALEX=off` is the machine's word, above a request's
    searches_left = int(os.environ.get("LITRAG_OPENALEX_SEARCHES", "50") if title_searches is None else title_searches)

    rows = [dict(r) for r in conn.execute("SELECT key, doi, pmid, pmcid, title, authors FROM papers ORDER BY added_at, key")]
    if keys is not None:
        wanted_keys = set(keys)
        rows = [r for r in rows if r["key"] in wanted_keys]
    kinds = [k for k, on in (("references", references), ("citations", citations),
                              ("openalex-references", references and use_oa), ("openalex-citations", citations and use_oa)) if on]
    asked = {(p, k) for p, k in conn.execute("SELECT paper, kind FROM harvests")}
    if not again and keys is None:
        rows = [r for r in rows if any((r["key"], k) not in asked for k in kinds)]
    held = _held_index(conn)
    resolver = _Library(conn)
    found: dict[str, dict[str, Any]] = {}  # ident -> what is known of it, before it is filed
    oa_hits: dict[str, dict[str, Any]] = {}  # ident -> OpenAlex's record of it, as a candidate's hit
    # (citing, cited, origin, side, ref_no, harvest kind): the unheld side an ident until filed
    edges: list[tuple[str, str, str, str, int | None, str]] = []
    direct: set[tuple[str, str, str, int | None]] = set()  # between held papers
    done: list[tuple[str, str, str, int]] = []  # the asks that were answered, marked once the round is filed
    stats: dict[str, Any] = {"papers": len(rows), "entries": 0, "held": 0, "identified": 0, "unidentified": 0, "asked": 0, "added": 0, "errors": [],
                             "openalex": {"on": use_oa, "asked": 0, "works": 0, "matched": 0, "searches": 0, "spent": False}}
    oas = stats["openalex"]
    w_refs: dict[str, list[str]] = {}  # a held paper's references, as OpenAlex ids, to fetch once for all papers
    w_of: dict[str, str] = {}  # a held paper's own OpenAlex id

    def note(ident: str, paper_round: int, query: str, seen: dict[str, Any]) -> None:
        f = found.setdefault(ident, {"round": paper_round + 1, "query": query, "seen": {}})
        f["round"] = min(f["round"], paper_round + 1)
        for k, v in seen.items():
            if v and not f["seen"].get(k):
                f["seen"][k] = v

    def openalex_work(p: dict[str, Any]) -> dict[str, Any] | None:
        ident = _paper_ident(p)
        if ident is None:
            return None
        w = oa.work(ident, timeout=timeout)
        oas["asked"] += 1
        if w is not None:
            w_of[p["key"]] = oa.short_id(w.get("id")) or ""
            if not p["authors"]:
                _fill_record(conn, p["key"], w)
        return w

    def spent(e: Exception) -> None:
        if not oas["spent"]:
            stats["errors"].append(f"OpenAlex: {e}")
        oas["spent"] = True

    for i, p in enumerate(rows):
        key = p["key"]
        say({"event": "progress", "op": "round", "done": i, "total": len(rows), "label": f"Citations of {i + 1} of {len(rows)}: {(p['title'] or key)[:60]}"})
        r = round_of(conn, key)
        epmc_refs = None
        listed = False  # whether either source gave the paper's reference list

        def due(kind: str) -> bool:
            if not network or kind not in kinds or not (again or keys is not None or (key, kind) not in asked):
                return False
            return bool(p["pmid"] or p["pmcid"]) if not kind.startswith("openalex") else _paper_ident(p) is not None

        if "references" in kinds:
            failed = False
            if due("references"):
                try:
                    epmc_refs = acquire.references_of(p["pmid"], p["pmcid"], timeout=timeout)
                    stats["asked"] += 1
                    listed = bool(epmc_refs)
                except (acquire.AcquireError, OSError) as e:
                    stats["errors"].append(f"{key}: references: {e}")
                    failed = True  # not asked, so not marked asked: the next round tries again
            if due("openalex-references"):
                try:
                    w = openalex_work(p)
                    if w is not None and w.get("referenced_works"):
                        w_refs[key] = [x for x in (oa.short_id(v) for v in w["referenced_works"]) if x]
                        listed = True
                    else:
                        done.append((key, "openalex-references", now_iso(), 0))  # OpenAlex holds no list of it: asked all the same
                except oa.Budget as e:
                    spent(e)
                except OSError as e:
                    stats["errors"].append(f"{key}: OpenAlex: {e}")
            n_found = 0
            unnamed: list[dict[str, Any]] = []  # entries naming nothing, for a search when no list came
            # the paper's own list, as read here
            for ref in conn.execute("SELECT ref_no, text, doi, pmid, year, first_author, title FROM refs WHERE paper = ? ORDER BY ref_no", (key,)).fetchall():
                ref = dict(ref)
                stats["entries"] += epmc_refs is None
                hit, _how = resolver.resolve(ref, key)
                if hit is not None:
                    direct.add((key, hit["key"], "refs", ref["ref_no"]))
                    n_found += 1
                    continue
                ident = acquire.ident_of(pmid=ref["pmid"]) or acquire.ident_of(doi=ref["doi"])
                if ident is None:
                    unnamed.append(ref)
                    continue
                if ident in held:  # another paper held, or the paper's own identifier: no candidate either way
                    if held[ident] != key:
                        direct.add((key, held[ident], "refs", ref["ref_no"]))
                        n_found += 1
                    continue
                note(ident, r, f"cited by {key}", {"title": ref["title"], "year": ref["year"], "authors": ref["first_author"], "doi": ref["doi"]})
                edges.append((key, ident, "refs", "cited", ref["ref_no"], "references"))
                n_found += 1
            # Europe PMC's list of it
            for x in epmc_refs or []:
                stats["entries"] += 1
                ident = _epmc_ident(x)
                if ident is None:
                    stats["unidentified"] += 1
                    continue
                if ident in held:
                    if held[ident] != key:
                        direct.add((key, held[ident], "europepmc", None))
                    n_found += 1
                    continue
                note(ident, r, f"cited by {key}", {"title": acquire.plain_text(x.get("title")), "year": str(x.get("pubYear") or "") or None,
                                                   "authors": str(x.get("authorString") or "").rstrip(".") or None, "journal": x.get("journalAbbreviation")})
                edges.append((key, ident, "europepmc", "cited", None, "references"))
                n_found += 1
            # no list from either source: an entry naming nothing is searched by its words
            if not listed and use_oa:
                for ref in unnamed:
                    if oas["spent"] or searches_left <= 0:
                        stats["unidentified"] += epmc_refs is None
                        continue
                    try:
                        searches_left -= 1
                        oas["searches"] += 1
                        w = oa.match(ref, timeout=timeout)
                    except oa.Budget as e:
                        spent(e)
                        w = None
                    except OSError as e:
                        stats["errors"].append(f"{key}: OpenAlex search: {e}")
                        w = None
                    ident = oa.ident(w) if w else None
                    if ident is None:
                        stats["unidentified"] += epmc_refs is None
                        continue
                    oas["matched"] += 1
                    if ident in held:
                        if held[ident] != key:
                            direct.add((key, held[ident], "openalex", ref["ref_no"]))
                        continue
                    oa_hits[ident] = oa.hit(w)  # type: ignore[arg-type]
                    note(ident, r, f"cited by {key}", {})
                    edges.append((key, ident, "openalex", "cited", ref["ref_no"], "references"))
            else:
                stats["unidentified"] += len(unnamed) if epmc_refs is None else 0
            if not failed:
                done.append((key, "references", now_iso(), n_found))
        if "citations" in kinds and due("citations"):
            try:
                citing = acquire.citations_of(p["pmid"], p["pmcid"], timeout=timeout, most=most_citing) or []
                stats["asked"] += 1
                for x in citing:
                    ident = _epmc_ident(x)
                    if ident is None:
                        continue
                    if ident in held:
                        if held[ident] != key:
                            direct.add((held[ident], key, "europepmc", None))
                        continue
                    note(ident, r, f"cites {key}", {"title": acquire.plain_text(x.get("title")), "year": str(x.get("pubYear") or "") or None,
                                                    "authors": str(x.get("authorString") or "").rstrip(".") or None, "journal": x.get("journalAbbreviation")})
                    edges.append((ident, key, "europepmc", "citing", None, "citations"))
                done.append((key, "citations", now_iso(), len(citing)))
            except (acquire.AcquireError, OSError) as e:
                stats["errors"].append(f"{key}: citations: {e}")  # not marked asked: the next round tries again
        if due("openalex-citations") and not oas["spent"]:
            try:
                w_id = w_of.get(key) or oa.short_id((openalex_work(p) or {}).get("id"))
                ws = oa.citing(w_id, most=most_citing, timeout=timeout) if w_id else []
                for w in ws:
                    ident = oa.ident(w)
                    if ident is None:
                        continue
                    if ident in held:
                        if held[ident] != key:
                            direct.add((held[ident], key, "openalex", None))
                        continue
                    oa_hits.setdefault(ident, oa.hit(w))
                    note(ident, r, f"cites {key}", {})
                    edges.append((ident, key, "openalex", "citing", None, "openalex-citations"))
                done.append((key, "openalex-citations", now_iso(), len(ws)))
            except oa.Budget as e:
                spent(e)
            except OSError as e:
                stats["errors"].append(f"{key}: OpenAlex citations: {e}")

    # OpenAlex's reference lists, the works in them fetched once for every paper
    wanted = sorted({w for ids in w_refs.values() for w in ids})
    if wanted:
        try:
            got, was_spent = oa.works(wanted, timeout=timeout, on_batch=lambda n, t: say({"event": "progress", "op": "round", "done": n, "total": t, "label": f"Fetching {len(wanted)} works from OpenAlex ({n + 1} of {t})"}))
            if was_spent and not oas["spent"]:
                spent(oa.Budget("OpenAlex's daily budget is spent: the rest were fetched one by one, which costs nothing"))
            oas["works"] = len(got)
            for key, ids in w_refs.items():
                r = round_of(conn, key)
                n = 0
                for w_id in ids:
                    w = got.get(w_id)
                    ident = oa.ident(w) if w else None
                    if ident is None:
                        continue
                    n += 1
                    if ident in held:
                        if held[ident] != key:
                            direct.add((key, held[ident], "openalex", None))
                        continue
                    oa_hits.setdefault(ident, oa.hit(w))  # type: ignore[arg-type]
                    note(ident, r, f"cited by {key}", {})
                    edges.append((key, ident, "openalex", "cited", None, "openalex-references"))
                done.append((key, "openalex-references", now_iso(), n))
        except OSError as e:
            stats["errors"].append(f"OpenAlex: {e}")  # those papers not marked asked: the next round tries again

    # every identified work a candidate: those known already as they are, the rest looked up in
    # Europe PMC (its record carries what a fetch needs); a work only OpenAlex names is not asked
    cands = _cand_index(conn)
    unknown = [ident for ident in found if ident not in cands and not ident.startswith("openalex:")]
    records: dict[str, dict[str, Any]] = {}
    if network and unknown:
        records, lost = acquire.lookup(unknown, timeout=max(timeout, 60), on_batch=lambda n, t: say({"event": "progress", "op": "round", "done": n, "total": t, "label": f"Looking up {len(unknown)} works in Europe PMC ({n + 1} of {t})"}))
        missed = set(lost)
        if missed:
            # without its record a work would be filed with no PMCID and no open-access flags, and
            # stay so: those are left for the next round, and the papers naming them not marked asked
            stats["errors"].append(f"lookup: {len(missed)} of {len(unknown)} works could not be looked up in Europe PMC; the next round asks again")
            unfinished = {(a if side == "cited" else b, kind) for a, b, _o, side, _n, kind in edges if (b if side == "cited" else a) in missed}
            done = [d for d in done if (d[0], d[1]) not in unfinished]
            found = {i: f for i, f in found.items() if i not in missed}
            edges = [e for e in edges if (e[1] if e[3] == "cited" else e[0]) not in missed]
    now = now_iso()
    added = 0
    ident_cand: dict[str, int] = {}
    with conn:
        # Europe PMC's records first, so a work OpenAlex knows by another DOI meets its candidate
        for ident, f in sorted(found.items(), key=lambda kv: kv[0] not in records):
            hits = [h for h in (records.get(ident), oa_hits.get(ident)) if h] or [_own_hit(ident, f["seen"])]
            cid = cands.get(ident)
            if cid is None and not records.get(ident) and oa_hits.get(ident):
                # a work only OpenAlex's record names may be a candidate already, under another
                # identity: one of its other identifiers, or the same title, year and first author
                h = oa_hits[ident]
                cid = next((cands[a] for a in (acquire.ident_of(pmid=h.get("pmid")), acquire.ident_of(doi=h.get("doi")), acquire.ident_of(pmcid=h.get("pmcid")), acquire.ident_of(openalex=h.get("openalex"))) if a in cands), None)
                if cid is None:
                    cid = _same_work(conn, h)
            if cid is None:
                cid, new = acquire.upsert_candidate(conn, hits[0], query=f["query"], now=now, round=f["round"])
                added += int(new)
            else:
                conn.execute("UPDATE candidates SET round = ? WHERE cand_id = ? AND round > ?", (f["round"], cid, f["round"]))
            for h in hits:
                acquire.fill_candidate(conn, cid, h)  # what one record lacks, the other fills
                for alias in (acquire.ident_of(pmid=h.get("pmid")), acquire.ident_of(doi=h.get("doi")), acquire.ident_of(pmcid=h.get("pmcid")), acquire.ident_of(openalex=h.get("openalex"))):
                    if alias:
                        cands.setdefault(alias, cid)
            ident_cand[ident] = cid
    stats["identified"] = len(found)
    acquire.reconcile(conn)
    with conn:
        conn.executemany("INSERT OR REPLACE INTO harvests VALUES (?, ?, ?, ?)", done)
        for citing, cited, origin, ref_no in direct:
            conn.execute("INSERT OR IGNORE INTO cites VALUES (?, ?, ?, ?)", (citing, cited, origin, ref_no))
        for a, b, origin, side, ref_no, _kind in edges:
            if side == "cited":
                citing, cited = a, _work_of(conn, ident_cand[b])
            else:
                citing, cited = _work_of(conn, ident_cand[a]), b
            if citing != cited:
                conn.execute("INSERT OR IGNORE INTO cites VALUES (?, ?, ?, ?)", (citing, cited, origin, ref_no))
    sync(conn)
    stats["held"] = len({(c, d) for c, d, _o, _n in direct})
    stats["added"] = added
    say({"event": "progress", "op": "round", "done": len(rows), "total": len(rows), "label": "Citations filed"})
    return stats


# ---------------------------------------------------------------- the picture


def graph(conn: sqlite3.Connection, candidates: str = "cited", min_cited: int = 2) -> dict[str, Any]:
    """The works and the citations between them, for the window to draw: every held paper, and
    candidates — `none`, those cited (or citing) at least `min_cited` held papers (`cited`), or
    `all`. Each node `{id, paper, cand_id, state, status, title, label, year, first_author,
    journal, round, cited_here, cites_here, type}`; each edge `{src, dst, origin}`, src citing
    dst, `origin` every source that names it, joined: "europepmc+openalex+refs"."""
    sync(conn)
    nodes = [dict(r) for r in conn.execute("SELECT * FROM works")]
    held = {n["work"] for n in nodes if n["state"] == "held"}
    pairs: dict[tuple[str, str], set[str]] = {}
    for citing, cited, origin in conn.execute("SELECT citing, cited, origin FROM cites"):
        pairs.setdefault((citing, cited), set()).add(origin)
    touching: dict[str, set[str]] = {}
    for citing, cited in pairs:
        if citing in held:
            touching.setdefault(cited, set()).add(citing)
        if cited in held:
            touching.setdefault(citing, set()).add(cited)

    def keep(n: dict[str, Any]) -> bool:
        if n["state"] == "held":
            return True
        if candidates == "all":
            return True
        if candidates == "none" or n["status"] == "dismissed":
            return False
        return len(touching.get(n["work"], ())) >= max(1, int(min_cited))

    kept = [n for n in nodes if keep(n)]
    ids = {n["work"] for n in kept}
    families = {w: f for w, f in conn.execute("SELECT work, family FROM authors WHERE pos = 1")}
    out_nodes = []
    for n in kept:
        who = families.get(n["work"]) or (n["first_author"] or "").split(" ")[0] or (n["title"] or "?").split(" ")[0]
        out_nodes.append({"id": n["work"], "paper": n["paper"], "cand_id": n["cand_id"], "state": n["state"], "status": n["status"],
                          "title": n["title"], "label": f"{who} {n['year']}" if n["year"] else who, "year": n["year"],
                          "first_author": n["first_author"], "journal": n["journal"], "round": n["round"],
                          "cited_here": n["cited_here"], "cites_here": n["cites_here"], "type": n["type"]})
    out_edges = [{"src": a, "dst": b, "origin": "+".join(sorted(o))} for (a, b), o in sorted(pairs.items()) if a in ids and b in ids]
    return {"nodes": out_nodes, "edges": out_edges,
            "hidden": sum(1 for n in nodes if n["work"] not in ids)}
