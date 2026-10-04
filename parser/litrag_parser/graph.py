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
from pathlib import Path
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
CREATE TABLE IF NOT EXISTS ref_lists (
  paper TEXT NOT NULL,     -- the held paper whose reference list it is
  source TEXT NOT NULL,    -- europepmc | openalex | openalex-search
  ord INTEGER NOT NULL,    -- europepmc: the entry's place in the list (citedOrder); openalex: OpenAlex's order, no place;
                           -- openalex-search: the entry (ref_no) whose words found it
  ident TEXT,              -- the work it names, when the source knew: pmid:… | doi:… | pmcid:… | openalex:W…
  title TEXT, year TEXT, first_author TEXT,
  volume TEXT, first_page TEXT, -- what an entry printing no title is matched by, with its author and year
  PRIMARY KEY (paper, source, ord)
);
CREATE TABLE IF NOT EXISTS ref_works (
  paper TEXT NOT NULL,     -- the citing paper held
  ref_no INTEGER NOT NULL, -- the entry of its reference list (refs)
  work TEXT NOT NULL,      -- the work the entry names: a papers.key, or 'cand:<id>'
  how TEXT NOT NULL,       -- doi | pmid | title (a held paper's whole title in it) | europepmc (Europe PMC's entry
                           -- at its place, first author and year agreeing) | openalex (a work of OpenAlex's list, its
                           -- whole title, year and first author in it) | openalex-search (found by its own words)
  PRIMARY KEY (paper, ref_no)
);
CREATE INDEX IF NOT EXISTS ref_works_work ON ref_works(work);
CREATE TABLE IF NOT EXISTS harvests (
  paper TEXT NOT NULL,     -- the held paper asked about
  kind TEXT NOT NULL,      -- references | citations | openalex-references | openalex-citations | local (its list
                           -- linked, as of `at`: its reading's time)
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


#: Every passage that cites a work: the in-text citation (`citations`: a node and the entry it
#: names) joined to the work that entry names (`ref_works`).
PASSAGES_VIEW = """
CREATE VIEW passage_cites AS
SELECT c.paper, c.node_id, c.ref_no, c.marker, r.work, r.how
FROM citations c JOIN ref_works r ON r.paper = c.paper AND r.ref_no = c.ref_no
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    acquire.ensure_schema(conn)
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(ref_lists)")}
    for col in ("volume", "first_page"):
        if col not in have:  # lists kept before an entry with no title could be matched by its print
            conn.execute(f"ALTER TABLE ref_lists ADD COLUMN {col} TEXT")
    for name, sql in (("works", WORKS_VIEW), ("passage_cites", PASSAGES_VIEW)):
        have = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'view' AND name = ?", (name,)).fetchone()
        if have is None or " ".join(str(have[0]).split()) != " ".join(sql.split()):
            conn.execute(f"DROP VIEW IF EXISTS {name}")
            conn.execute(sql.strip())
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
    authors from its own record; a candidate's from its record; every reference entry linked to the
    work it names (`link_refs`). Each part is done again only when what it reads has changed, so a `SELECT` that calls
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
    linked = link_refs(conn)
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


def _fold_family(name: Any) -> str | None:
    """The first author's family name of an author string ("Onck PR, Koeman T" → "onck"), folded."""
    first = str(name or "").split(",")[0].strip()
    family = split_name(first)[0] if first else None
    return " ".join(re.sub(r"[^a-z0-9]+", " ", _fold(family)).split()) if family else None


def _at_its_place(entry: dict[str, Any], row: dict[str, Any]) -> bool:
    """Whether Europe PMC's entry at the same place in the list is this entry: its first author's
    family name in the entry's words, and a year it prints within one of the entry's (or none). A list read from a
    PDF can run out of step with the printed one; these keep a place from being taken on trust."""
    from .openalex import _norm

    from .openalex import year_agrees

    family = _fold_family(row.get("first_author"))
    if not family or f" {family} " not in f" {_norm(entry.get('text'))} ":
        return False
    return year_agrees(entry, row.get("year"))


#: How entries are matched to works: raised whenever that changes, so libraries linked before are linked again.
LINKER = 3


def link_refs(conn: sqlite3.Connection) -> int:
    """Every entry of every held paper's reference list linked to the work it names (`ref_works`),
    and a `cites` row (origin `refs`) for each: no network — from the entry's own identifiers, a
    held paper's whole title in it, and the lists the rounds kept (`ref_lists`): Europe PMC's entry
    at the same place when its first author and year agree, else the one work of either list whose
    whole title, year and first author the entry carries. An entry none of these names stays
    unlinked (invariant 5). Done again whenever the papers, their lists, the candidates or the
    lists change, so a reread or a rebuild is linked again without asking anything. An entry that
    prints no title is matched by what it does print: first author, year, volume and first page.

    The stamp folds in the linker's own version, so a change to how entries are matched reaches a
    library linked before it."""
    from .lineage import _Library
    from .openalex import Entry, Work, by_print_prepared, names_prepared

    changed, stamp = _stamp(conn, "refs-linked",
                            f"SELECT {LINKER}, COUNT(*), MAX(rowid), GROUP_CONCAT(parsed_at), TOTAL(length(doi)), TOTAL(length(pmid)), TOTAL(length(title)) FROM papers"
                            " UNION ALL SELECT NULL, COUNT(*), MAX(rowid), NULL, NULL, NULL, NULL FROM refs"
                            " UNION ALL SELECT NULL, COUNT(*), MAX(cand_id), COUNT(paper_key), TOTAL(length(doi)), TOTAL(length(pmid)), TOTAL(length(openalex)) FROM candidates"
                            " UNION ALL SELECT NULL, COUNT(*), MAX(rowid), NULL, NULL, NULL, NULL FROM ref_lists")
    if not changed:
        return 0
    held = _held_index(conn)
    held_cands = {c: k for c, k in conn.execute("SELECT cand_id, paper_key FROM candidates WHERE paper_key IN (SELECT key FROM papers)")}
    work_of = {ident: held_cands.get(cid, f"cand:{cid}") for ident, cid in _cand_index(conn).items()}
    lists: dict[str, list[dict[str, Any]]] = {}
    for r in conn.execute("SELECT * FROM ref_lists ORDER BY paper, source, ord"):
        lists.setdefault(r["paper"], []).append(dict(r))
    # each list's works prepared once, filed by the last word of their first author's family name
    by_family: dict[str, dict[str, list[tuple[dict[str, Any], Work]]]] = {}
    no_family: dict[str, list[tuple[dict[str, Any], Work]]] = {}
    for paper, rows_of in lists.items():
        for r in rows_of:
            if not r["ident"] or r["source"] == "openalex-search":
                continue
            pw = Work(r["title"], r["year"], _fold_family(r["first_author"]), r.get("volume"), r.get("first_page"))
            if pw.family:
                by_family.setdefault(paper, {}).setdefault(pw.family.split()[-1], []).append((r, pw))
            else:
                no_family.setdefault(paper, []).append((r, pw))
    resolver = _Library(conn)

    def work(ident: str | None) -> str | None:
        return (held.get(ident) or work_of.get(ident)) if ident else None

    links: list[tuple[str, int, str, str]] = []
    for key, in conn.execute("SELECT key FROM papers").fetchall():
        rows = lists.get(key, [])
        placed = {r["ord"]: r for r in rows if r["source"] == "europepmc"}
        searched = {r["ord"]: r for r in rows if r["source"] == "openalex-search"}
        for e in conn.execute("SELECT ref_no, text, doi, pmid, year, first_author, title FROM refs WHERE paper = ? ORDER BY ref_no", (key,)).fetchall():
            e = dict(e)
            hit, how = resolver.resolve(e, key)
            w = hit["key"] if hit is not None else None
            if w is None:
                for kind, ident in (("pmid", acquire.ident_of(pmid=e["pmid"])), ("doi", acquire.ident_of(doi=e["doi"]))):
                    if work(ident):
                        w, how = work(ident), kind
                        break
            if w is None and e["ref_no"] in placed and _at_its_place(e, placed[e["ref_no"]]):
                w, how = work(placed[e["ref_no"]]["ident"]), "europepmc"
            if w is None and e["ref_no"] in searched:
                w, how = work(searched[e["ref_no"]]["ident"]), "openalex-search"
            if w is None:
                pe = Entry(e)
                # only the works whose first author the entry names can be it (and those with no
                # author on record, for the title alone): a handful of the list, not all of it
                near = [x for fam in pe.tokens & by_family.get(key, {}).keys() for x in by_family[key][fam]] + no_family.get(key, [])
                near.sort(key=lambda x: (x[0]["source"], x[0]["ord"]))  # a work both lists name is credited to the same one every time
                for match in (names_prepared, by_print_prepared):
                    named: dict[str, str] = {}
                    for x in near:
                        if match(pe, x[1]):
                            named.setdefault(x[0]["ident"], x[0]["source"])
                    if len({work(i) for i in named} - {None}) == 1:
                        ident = next(i for i in named if work(i))
                        w, how = work(ident), named[ident]
                        break
            if w and w != key:
                links.append((key, e["ref_no"], w, how))
    with conn:
        conn.execute("DELETE FROM ref_works")
        conn.executemany("INSERT OR IGNORE INTO ref_works VALUES (?, ?, ?, ?)", links)
        before = conn.execute("SELECT COUNT(*) FROM cites WHERE origin = 'refs'").fetchone()[0]
        conn.execute("DELETE FROM cites WHERE origin = 'refs'")
        conn.execute("INSERT OR IGNORE INTO cites SELECT paper, work, 'refs', MIN(ref_no) FROM ref_works GROUP BY paper, work")
        after = conn.execute("SELECT COUNT(*) FROM cites WHERE origin = 'refs'").fetchone()[0]
        conn.execute("INSERT OR REPLACE INTO graph_state VALUES ('refs-linked', ?)", (stamp,))
    return max(0, after - before)


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


def _own_identifiers(conn: sqlite3.Connection, rows: list[dict[str, Any]], timeout: float) -> None:
    """A held paper read from a PDF knows its DOI and seldom its PMID or PMCID — and without them
    Europe PMC's list of its references cannot be asked. Its record is looked up by the DOI, and
    what it lacks is filled (never replaced), in the rows and in `papers`."""
    lacking = {acquire.ident_of(doi=p["doi"]): p for p in rows if p["doi"] and not (p["pmid"] or p["pmcid"])}
    lacking.pop(None, None)
    if not lacking:
        return
    try:
        got, _ = acquire.lookup(list(lacking), timeout=max(timeout, 60))
    except (acquire.AcquireError, OSError):
        return
    with conn:
        for ident, hit in got.items():
            p = lacking[ident]
            p["pmid"], p["pmcid"] = p["pmid"] or hit.get("pmid"), p["pmcid"] or hit.get("pmcid")
            conn.execute("UPDATE papers SET pmid = COALESCE(pmid, ?), pmcid = COALESCE(pmcid, ?) WHERE key = ?", (hit.get("pmid"), hit.get("pmcid"), p["key"]))


REF_LIST_COLS = "paper, source, ord, ident, title, year, first_author, volume, first_page"


def _first_page(pages: Any) -> str | None:
    """"1913-1927" → "1913"; "S64-S68" → "S64"; "e32566" → "e32566"."""
    first = re.split(r"\s*[-–—]\s*", str(pages or "").strip())[0]
    return first or None


def _biblio(w: dict[str, Any]) -> tuple[str | None, str | None]:
    b = w.get("biblio") or {}
    return (str(b.get("volume") or "") or None, str(b.get("first_page") or "") or None)


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

    rows = [dict(r) for r in conn.execute("SELECT key, doi, pmid, pmcid, title, authors, format FROM papers ORDER BY added_at, key")]
    if keys is not None:
        wanted_keys = set(keys)
        rows = [r for r in rows if r["key"] in wanted_keys]
    kinds = [k for k, on in (("references", references), ("citations", citations),
                              ("openalex-references", references and use_oa), ("openalex-citations", citations and use_oa)) if on]
    asked = {(p, k) for p, k in conn.execute("SELECT paper, kind FROM harvests")}
    if not again and keys is None:
        rows = [r for r in rows if any((r["key"], k) not in asked for k in kinds)]
    if network:
        _own_identifiers(conn, rows, timeout)
    held = _held_index(conn)
    resolver = _Library(conn)
    found: dict[str, dict[str, Any]] = {}  # ident -> what is known of it, before it is filed
    printed: set[str] = set()  # what only a PDF's printed words named: trusted once a source knows it
    from_lists: set[str] = set()  # what a source's list named
    oa_hits: dict[str, dict[str, Any]] = {}  # ident -> OpenAlex's record of it, as a candidate's hit
    # (citing, cited, origin, side, ref_no, harvest kind): the unheld side an ident until filed
    edges: list[tuple[str, str, str, str, int | None, str]] = []
    direct: set[tuple[str, str, str, int | None]] = set()  # between held papers
    done: list[tuple[str, str, str, int]] = []  # the asks that were answered, marked once the round is filed
    stats: dict[str, Any] = {"papers": len(rows), "entries": 0, "held": 0, "identified": 0, "unidentified": 0, "asked": 0, "added": 0, "errors": [],
                             "openalex": {"on": use_oa, "asked": 0, "works": 0, "matched": 0, "searches": 0, "spent": False}}
    oas = stats["openalex"]
    w_refs: dict[str, list[str]] = {}  # a held paper's references, as OpenAlex ids, to fetch once for all papers
    kept: dict[tuple[str, str], list[tuple[Any, ...]]] = {}  # (paper, source) -> its list, kept as `ref_lists` rows
    w_of: dict[str, str] = {}  # a held paper's own OpenAlex id

    def note(ident: str, paper_round: int, query: str, seen: dict[str, Any], *, source: bool = True) -> None:
        if source:
            from_lists.add(ident)
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
                note(ident, r, f"cited by {key}", {"title": ref["title"], "year": ref["year"], "authors": ref["first_author"], "doi": ref["doi"]}, source=False)
                edges.append((key, ident, "refs", "cited", ref["ref_no"], "references"))
                if p["format"] != "jats":
                    printed.add(ident)
                n_found += 1
            # Europe PMC's list of it, kept: its places line its entries up with the paper's own
            if epmc_refs is not None:
                kept[(key, "europepmc")] = [(key, "europepmc", int(x.get("citedOrder") or n + 1), _epmc_ident(x), acquire.plain_text(x.get("title")),
                                             str(x.get("pubYear") or "") or None, str(x.get("authorString") or "").rstrip(".") or None,
                                             str(x.get("volume") or "") or None, _first_page(x.get("pageInfo")))
                                            for n, x in enumerate(epmc_refs)]
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
                    kept.setdefault((key, "openalex-search"), []).append((key, "openalex-search", ref["ref_no"], ident, w.get("title"), str(w.get("publication_year") or "") or None, oa.first_author(w),  # type: ignore[union-attr]
                                                                       *_biblio(w)))
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
                kept[(key, "openalex")] = [(key, "openalex", i, oa.ident(got[w_id]), got[w_id].get("title"), str(got[w_id].get("publication_year") or "") or None,
                                            oa.first_author(got[w_id]), *_biblio(got[w_id])) for i, w_id in enumerate(ids, start=1) if w_id in got]
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
    # A DOI read off a PDF can be one the line break mangled ("10.1158/00085472…" for 0008-5472):
    # it is filed only when Europe PMC or OpenAlex knows it, else the entry is left to the lists
    for ident in sorted(printed - from_lists):
        if ident not in found or records.get(ident) or oa_hits.get(ident) or ident in cands:
            continue
        w = None
        if use_oa and not oas["spent"]:
            try:
                w = oa.work(ident, timeout=timeout)
            except (oa.Budget, OSError):
                w = None
        if w is not None and oa.ident(w):
            oa_hits[ident] = oa.hit(w)
            continue
        del found[ident]
        stats["unverified"] = stats.get("unverified", 0) + 1
    edges = [e for e in edges if (e[1] if e[3] == "cited" else e[0]) in found or (e[1] if e[3] == "cited" else e[0]) in held]
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
        for (paper, source), rows_kept in kept.items():
            conn.execute("DELETE FROM ref_lists WHERE paper = ? AND source = ?", (paper, source))
            conn.executemany(f"INSERT OR REPLACE INTO ref_lists ({REF_LIST_COLS}) VALUES ({', '.join('?' * 9)})", rows_kept)
        # a `refs` citation is an entry linked to its work, which `link_refs` derives from these rows
        for citing, cited, origin, ref_no in direct:
            if origin != "refs":
                conn.execute("INSERT OR IGNORE INTO cites VALUES (?, ?, ?, ?)", (citing, cited, origin, ref_no))
        for a, b, origin, side, ref_no, _kind in edges:
            if origin == "refs":
                continue
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
    dst, `origin` every source that names it, joined ("europepmc+openalex+refs"), `passages` how
    many of src's passages cite dst in their text (`passage_cites`)."""
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
    passages = {(a, b): n for a, b, n in conn.execute("SELECT paper, work, COUNT(DISTINCT node_id) FROM passage_cites GROUP BY paper, work")}
    out_edges = [{"src": a, "dst": b, "origin": "+".join(sorted(o)), "passages": passages.get((a, b), 0)}
                 for (a, b), o in sorted(pairs.items()) if a in ids and b in ids]
    return {"nodes": out_nodes, "edges": out_edges,
            "hidden": sum(1 for n in nodes if n["work"] not in ids)}


# ---------------------------------------------------------------- the passages, and the next papers to read


def passages_citing(conn: sqlite3.Connection, work: str, limit: int = 200) -> list[dict[str, Any]]:
    """Every passage of the papers held that cites `work` in its text — the node, where it sits,
    its words, the marker that named the work and how the entry was linked to it: the chunks a
    paper read later is cited by, from the papers read before it."""
    sync(conn)
    rows = conn.execute(
        """SELECT pc.paper, p.title AS paper_title, p.year AS paper_year, pc.node_id, pc.ref_no, pc.marker, pc.how,
                  n.role, n.ancestry, n.page, n.text
           FROM passage_cites pc JOIN nodes n ON n.node_id = pc.node_id JOIN papers p ON p.key = pc.paper
           WHERE pc.work = ? ORDER BY p.year, pc.paper, n.depth, n.ordinal LIMIT ?""", (work, int(limit))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["ancestry"] = json.loads(d["ancestry"] or "[]")
        d["text"] = (d["text"] or "")[:600]
        out.append(d)
    return out


def next_to_read(conn: sqlite3.Connection, most: int = 20, min_cited: int = 1) -> list[dict[str, Any]]:
    """The candidates the papers held cite most and the library has not read: cited (or citing) by
    at least `min_cited` held papers, not yet fetched, dismissed or given up on — the most cited
    here first; among those cited as often, one that can be read now (an open XML, or an open paper
    with a PMCID the PDF routes ask by — a PMCID alone is no promise: PMC shows many papers it may
    not give out, measured 2026-10-04) before one that would wait for a person; then the most cited anywhere,
    then the newest — at most `most`."""
    sync(conn)
    return [dict(r) for r in conn.execute(
        """SELECT w.cand_id, w.work, w.title, w.year, w.first_author, w.status, w.cited_by,
                  (SELECT COUNT(DISTINCT x.citing) FROM cites x WHERE x.cited = w.work AND x.citing IN (SELECT key FROM papers))
                + (SELECT COUNT(DISTINCT x.cited) FROM cites x WHERE x.citing = w.work AND x.cited IN (SELECT key FROM papers)) AS held_links,
                  (c.has_xml = 1 OR (c.is_open_access = 1 AND c.pmcid IS NOT NULL)) AS readable
           FROM works w JOIN candidates c ON c.cand_id = w.cand_id
           WHERE w.state = 'candidate' AND w.status IN ('found', 'failed')
           ORDER BY held_links DESC, readable DESC, COALESCE(w.cited_by, 0) DESC, COALESCE(w.year, 0) DESC, w.cand_id""").fetchall()
        if r["held_links"] >= max(1, int(min_cited))][: max(0, int(most))]


# ---------------------------------------------------------------- how right the links are


def _paper_pairs(test: sqlite3.Connection, truth: sqlite3.Connection) -> list[tuple[str, str]]:
    """The papers two libraries both hold: by DOI, else PMID, else PMCID, else the same title."""
    def ids(conn: sqlite3.Connection) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, doi, pmid, pmcid, title in conn.execute("SELECT key, doi, pmid, pmcid, title FROM papers"):
            for i in (acquire.ident_of(doi=doi), acquire.ident_of(pmid=pmid), acquire.ident_of(pmcid=pmcid),
                      f"title:{' '.join(re.sub(r'[^a-z0-9]+', ' ', str(title or '').lower()).split())}" if title else None):
                if i:
                    out.setdefault(i, key)
        return out

    t, g = ids(test), ids(truth)
    pairs: dict[str, str] = {}
    for i, key in t.items():
        if i in g:
            pairs.setdefault(key, g[i])
    return sorted(pairs.items())


def _work_ids(conn: sqlite3.Connection, work: str) -> tuple[set[str], str | None]:
    """A work's identifiers (`doi:…`, `pmid:…`) and its title, held or a candidate."""
    if work.startswith("cand:"):
        r = conn.execute("SELECT doi, pmid, pmcid, title FROM candidates WHERE cand_id = ?", (int(work[5:]),)).fetchone()
    else:
        r = conn.execute("SELECT doi, pmid, pmcid, title FROM papers WHERE key = ?", (work,)).fetchone()
    if r is None:
        return set(), None
    return {i for i in (acquire.ident_of(doi=r[0]), acquire.ident_of(pmid=r[1]), acquire.ident_of(pmcid=r[2])) if i}, r[3]


def measure_links(test: sqlite3.Connection, truth: sqlite3.Connection, show: int = 0) -> dict[str, Any]:
    """How right one reading's entry links are (`ref_works`), against another reading of the same
    papers whose entries carry their own identifiers — the JATS of the papers a PDF library holds.
    For each paper both hold: every link is `right` when the work it names is one the truth's list
    names by DOI or PMID, else `unverified` when the truth has entries naming nothing it could be,
    else `wrong`; a right link is at the `right entry` when that truth entry's first author is in the
    linked entry's words. `reached` is the share of the truth's identified works some entry of the
    test reading is linked to. `show` lists that many unverified or wrong links to look at."""
    sync(test)
    papers, totals = [], {"entries": 0, "linked": 0, "right": 0, "right_entry": 0, "wrong": 0, "unverified": 0, "gold": 0, "reached": 0, "by": {}}
    shown: list[dict[str, Any]] = []
    for tkey, gkey in _paper_pairs(test, truth):
        gold: dict[str, dict[str, Any]] = {}
        unnamed = 0
        for r in truth.execute("SELECT ref_no, doi, pmid, first_author, year, text FROM refs WHERE paper = ?", (gkey,)):
            got = {i for i in (acquire.ident_of(doi=r[1]), acquire.ident_of(pmid=r[2])) if i}
            unnamed += not got
            for i in got:
                gold.setdefault(i, {"first_author": r[3], "year": r[4], "text": r[5]})
        distinct: list[set[str]] = []
        for r in truth.execute("SELECT doi, pmid FROM refs WHERE paper = ?", (gkey,)):
            got = {i for i in (acquire.ident_of(doi=r[0]), acquire.ident_of(pmid=r[1])) if i}
            if got and not any(got & d for d in distinct):
                distinct.append(got)
        row = {"paper": tkey, "entries": 0, "linked": 0, "right": 0, "right_entry": 0, "wrong": 0, "unverified": 0, "gold": len(distinct), "reached": 0}
        row["entries"] = test.execute("SELECT COUNT(*) FROM refs WHERE paper = ?", (tkey,)).fetchone()[0]
        reached: set[int] = set()
        for ref_no, work, how, text in test.execute(
                "SELECT rw.ref_no, rw.work, rw.how, r.text FROM ref_works rw JOIN refs r ON r.paper = rw.paper AND r.ref_no = rw.ref_no WHERE rw.paper = ?", (tkey,)).fetchall():
            row["linked"] += 1
            totals["by"].setdefault(how, {"linked": 0, "right": 0, "wrong": 0, "unverified": 0})
            totals["by"][how]["linked"] += 1
            ids, title = _work_ids(test, work)
            hit = next((gold[i] for i in ids if i in gold), None)
            if hit is not None:
                row["right"] += 1
                totals["by"][how]["right"] += 1
                reached |= {n for n, d in enumerate(distinct) if ids & d}
                family = _fold_family(hit["first_author"]) if hit["first_author"] else None
                from .openalex import _norm

                if family and f" {family} " in f" {_norm(text)} ":
                    row["right_entry"] += 1
                continue
            verdict = "unverified" if unnamed else "wrong"
            row[verdict] += 1
            totals["by"][how][verdict] += 1
            if len(shown) < show:
                shown.append({"paper": tkey, "ref_no": ref_no, "how": how, "verdict": verdict, "entry": (text or "")[:200], "work": work, "title": title})
        row["reached"] = len(reached)
        papers.append(row)
        for k in ("entries", "linked", "right", "right_entry", "wrong", "unverified", "gold", "reached"):
            totals[k] += row[k]
    judged = totals["right"] + totals["wrong"]
    totals["precision"] = round(totals["right"] / judged, 3) if judged else None
    totals["recall"] = round(totals["reached"] / totals["gold"], 3) if totals["gold"] else None
    return {"papers": papers, "totals": totals, "shown": shown}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from .store import open_store

    ap = argparse.ArgumentParser(prog="python -m litrag_parser.graph", description="The library as a graph of works: measure its entry links.")
    ap.add_argument("--lib", required=True, help="the library whose links are measured (its store's folder), e.g. the PDFs")
    ap.add_argument("--truth", required=True, help="a library holding the same papers whose entries carry their identifiers, e.g. their JATS")
    ap.add_argument("--show", type=int, default=0, help="list this many unverified or wrong links")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    test, truth = open_store(Path(a.lib) / "store.sqlite"), open_store(Path(a.truth) / "store.sqlite")
    try:
        out = measure_links(test, truth, show=a.show)
    finally:
        test.close()
        truth.close()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    t = out["totals"]
    print(f"{len(out['papers'])} papers in both; {t['entries']} entries, {t['linked']} linked: {t['right']} right ({t['right_entry']} at the right entry), "
          f"{t['wrong']} wrong, {t['unverified']} not verifiable; precision {t['precision']}, works reached {t['reached']} of {t['gold']} (recall {t['recall']})")
    for how, b in sorted(t["by"].items()):
        print(f"  {how:16} {b['linked']:4} linked, {b['right']:4} right, {b['wrong']:3} wrong, {b['unverified']:3} unverified")
    for p in out["papers"]:
        print(f"  {p['paper'][:40]:40} {p['entries']:3} entries, {p['linked']:3} linked, {p['right']:3} right, {p['wrong']:2} wrong, {p['unverified']:2} unverified, reached {p['reached']}/{p['gold']}")
    for s in out["shown"]:
        print(f"  [{s['verdict']}] {s['paper']} [{s['ref_no']}] by {s['how']}: {s['entry'][:110]!r} -> {s['title']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
