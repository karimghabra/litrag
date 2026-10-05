"""OpenAlex, beside Europe PMC: a work's references and citing works, and a reference matched by
its title, for the citation rounds (graph.py).

OpenAlex (`api.openalex.org`, OurResearch's open index of 300M+ works, all of it CC0) knows works
Europe PMC does not — engineering, physics and materials journals PubMed never indexed — and a
work's reference list for any DOI the publisher deposited references for. It is asked only with
identifiers (a DOI, a PMID, a PMCID, its own `W…` ids) and, for an entry that names none, the
entry's own words; never a paper's text (invariant 1; Karim, 2026-10-04).

What it costs decides how it is asked (its docs, 2026-10): one work by its id is free and
unlimited; a filtered list is $0.0001 a call, a search $0.001; with no key, everyone behind one IP
address shares $0.10 a day, and a free key (`LITRAG_OPENALEX_KEY`, sent as a bearer token, never
filled in for you) has $1 a day of its own. So a paper's references come from the free single
lookup; the works they name are fetched a hundred to a list call while the budget lasts and one
by one, free, once it is spent; what cites a paper and a match by title need the budget, and a
spent budget is said (`Budget`), not taken for an answer. `LITRAG_OPENALEX=off` turns it off;
`LITRAG_OPENALEX_URL` points it elsewhere (the tests and the end-to-end fixture).
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable

from .acquire import USER_AGENT, plain_text

OPENALEX = "https://api.openalex.org"
#: The fields a round reads of a work, and no more (`select`): a smaller answer, sooner.
FIELDS = ("id", "doi", "ids", "title", "publication_year", "publication_date", "authorships", "primary_location", "biblio",
          "cited_by_count", "open_access", "type", "referenced_works")
BATCH = 100  # ids ORed into one filter: OpenAlex's own limit
GAP = 0.05  # between requests: one at a time, far under its 100 a second


class Budget(RuntimeError):
    """OpenAlex's daily budget is spent (a 429 that says so): nothing more is asked of it today."""


def enabled() -> bool:
    return os.environ.get("LITRAG_OPENALEX", "on").strip().lower() not in ("off", "0", "false", "no")


def base(url: str | None = None) -> str:
    return (url or os.environ.get("LITRAG_OPENALEX_URL") or OPENALEX).rstrip("/")


_last = 0.0


def _get(path: str, params: dict[str, Any], timeout: float, url: str | None = None) -> dict[str, Any] | None:
    """One GET, its JSON; None for a 404 (no such work). A spent budget raises `Budget`; a busy
    answer (a 429 for speed, 502–504) is asked again after a pause."""
    global _last
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, safe=":|,")
    req = urllib.request.Request(f"{base(url)}{path}{'?' + q if q else ''}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    key = os.environ.get("LITRAG_OPENALEX_KEY", "").strip()
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    for attempt in range(4):
        wait = _last + GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:
                body = e.read().decode("utf-8", "replace")
                if "budget" in body.lower():
                    raise Budget("OpenAlex's daily budget is spent" + ("" if key else " (no key: the free budget is shared by every machine behind this address; a free key, LITRAG_OPENALEX_KEY, has its own)")) from e
            if e.code not in (429, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(1.5 * 2 ** attempt)
        finally:
            _last = time.monotonic()
    raise AssertionError("unreachable")


def short_id(w: Any) -> str | None:
    """`W4400292404` from `https://openalex.org/W4400292404`, or from itself."""
    m = re.search(r"\b([WAS]\d+)$", str(w or "").strip())
    return m.group(1) if m else None


def work(ident: str, *, timeout: float = 30, url: str | None = None) -> dict[str, Any] | None:
    """One work by `doi:…`, `pmid:…`, `pmcid:…` or its `W…` id — free. None when OpenAlex has none."""
    kind, _, value = ident.partition(":")
    path = {"doi": f"doi:{value}", "pmid": f"pmid:{value}", "pmcid": f"pmcid:{value}", "openalex": value}.get(kind, ident)
    return _get(f"/works/{urllib.parse.quote(path, safe=':/')}", {"select": ",".join(FIELDS)}, timeout, url)


def works(ids: Iterable[str], *, timeout: float = 30, url: str | None = None, on_batch: Callable[[int, int], None] | None = None) -> tuple[dict[str, dict[str, Any]], bool]:
    """Many works by their `W…` ids: `({W: work}, spent)`. A hundred to a list call while the
    budget lasts; once it is spent (`spent` True), the rest one by one, which costs nothing."""
    ids = list(dict.fromkeys(i for i in (short_id(x) for x in ids) if i))
    out: dict[str, dict[str, Any]] = {}
    spent = False
    batches = [ids[i:i + BATCH] for i in range(0, len(ids), BATCH)]
    for n, batch in enumerate(batches):
        if on_batch:
            on_batch(n, len(batches))
        if not spent:
            try:
                page = _get("/works", {"filter": f"openalex:{'|'.join(batch)}", "per_page": BATCH, "select": ",".join(FIELDS)}, timeout, url) or {}
                for w in page.get("results") or []:
                    if short_id(w.get("id")):
                        out[short_id(w.get("id"))] = w  # type: ignore[index]
                continue
            except Budget:
                spent = True
        for w_id in batch:
            w = work(f"openalex:{w_id}", timeout=timeout, url=url)
            if w is not None:
                out[w_id] = w
    return out, spent


def citing(w_id: str, *, most: int = 1000, timeout: float = 30, url: str | None = None) -> list[dict[str, Any]]:
    """The works that cite one, newest first, at most `most` — a list call per hundred, so it
    raises `Budget` when the budget is spent."""
    out: list[dict[str, Any]] = []
    cursor = "*"
    while cursor and len(out) < most:
        page = _get("/works", {"filter": f"cites:{short_id(w_id)}", "per_page": BATCH, "cursor": cursor, "sort": "publication_date:desc",
                               "select": ",".join(f for f in FIELDS if f != "referenced_works")}, timeout, url) or {}
        got = page.get("results") or []
        out.extend(got)
        cursor = (page.get("meta") or {}).get("next_cursor") if got else None
    return out[:most]


def search(text: str, year: str | int | None = None, *, n: int = 5, timeout: float = 30, url: str | None = None) -> list[dict[str, Any]]:
    """The works whose words best match `text` — a search, $0.001 — narrowed to a year either side
    of `year` when known."""
    params: dict[str, Any] = {"search": text, "per_page": n, "select": ",".join(f for f in FIELDS if f != "referenced_works")}
    if year and str(year).isdigit():
        params["filter"] = f"publication_year:{int(year) - 1}-{int(year) + 1}"
    return (_get("/works", params, timeout, url) or {}).get("results") or []


# ---------------------------------------------------------------- a work as a candidate


def _strip(prefix: str, v: Any) -> str | None:
    s = str(v or "").strip()
    if not s:
        return None
    return s[len(prefix):] if s.lower().startswith(prefix) else s


def ident(w: dict[str, Any]) -> str | None:
    """A work's identity as the rounds key it: `pmid:…`, else `doi:…`, else `pmcid:…`, else
    `openalex:W…` for a work no other register names."""
    ids = w.get("ids") or {}
    pmid = _strip("https://pubmed.ncbi.nlm.nih.gov/", ids.get("pmid"))
    doi = _strip("https://doi.org/", w.get("doi") or ids.get("doi"))
    pmcid = _strip("https://www.ncbi.nlm.nih.gov/pmc/articles/", ids.get("pmcid"))
    if pmid and pmid.strip("/").isdigit():
        return f"pmid:{pmid.strip('/')}"
    if doi:
        return f"doi:{doi.lower()}"
    if pmcid and re.fullmatch(r"(?i)pmc\d+/?", pmcid):
        return f"pmcid:{pmcid.strip('/').upper()}"
    w_id = short_id(w.get("id"))
    return f"openalex:{w_id}" if w_id else None


def hit(w: dict[str, Any]) -> dict[str, Any]:
    """An OpenAlex work in the shape `acquire.normalise_hit` gives a Europe PMC record, so it can
    be filed as a candidate the same way. Its open-access flag is OpenAlex's; whether Europe PMC
    serves an XML is Europe PMC's to say, so `has_xml` is never set from here."""
    ids = w.get("ids") or {}
    pmid = _strip("https://pubmed.ncbi.nlm.nih.gov/", ids.get("pmid"))
    pmcid = _strip("https://www.ncbi.nlm.nih.gov/pmc/articles/", ids.get("pmcid"))
    doi = _strip("https://doi.org/", w.get("doi") or ids.get("doi"))
    people = []
    for a in w.get("authorships") or []:
        who = a.get("author") or {}
        name = plain_text(who.get("display_name")) or plain_text(a.get("raw_author_name"))
        if name:
            people.append({"name": name, "family": None, "given": None, "initials": None,
                           "orcid": _strip("https://orcid.org/", who.get("orcid")), "openalex": short_id(who.get("id"))})
    source = ((w.get("primary_location") or {}).get("source") or {})
    oa = w.get("open_access") or {}
    title = plain_text(w.get("title"))
    return {
        "source": "openalex",
        "pmid": pmid.strip("/") if pmid and pmid.strip("/").isdigit() else None,
        "pmcid": pmcid.strip("/").upper() if pmcid else None,
        "doi": doi.lower() if doi else None,
        "title": title.rstrip(".") if title else None,
        "authors": ", ".join(p["name"] for p in people) or None,
        "author_list": people or None,
        "journal": plain_text(source.get("display_name")),
        "year": str(w["publication_year"]) if w.get("publication_year") else None,
        "published": w.get("publication_date") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(w.get("publication_date") or "")) else None,
        "abstract": None,
        "is_open_access": bool(oa.get("is_oa")),
        "has_pdf": False,
        "has_xml": False,
        "cited_by": int(w.get("cited_by_count") or 0),
        "pub_types": [w["type"]] if w.get("type") else [],
        "openalex": short_id(w.get("id")),
        "oa_url": oa.get("oa_url") or None,
    }


# ---------------------------------------------------------------- a reference matched by its words


def _norm(s: Any) -> str:
    """Lowercase letters and digits, accents folded ("García" is "garcia"), everything else a space."""
    folded = "".join(ch for ch in unicodedata.normalize("NFKD", str(s or "")) if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded.lower()).split())


#: A title shorter than this is too common to stand for one work found inside an entry's words.
MATCH_TITLE_WORDS = 4
#: …nor one this short once squeezed, which is how a title is looked for: a PDF's line break takes a
#: hyphen or a space out of a title ("plateletrich plasma"), so spaces and marks count for nothing
MATCH_TITLE_CHARS = 24
_YEARS = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


class Entry:
    """A reference entry made ready for matching once: its words normalised, squeezed (no spaces),
    as a set, and the years it prints. Matching an entry against a list of works asks these over
    and over; preparing them once is what keeps a library of thousands of entries quick."""

    __slots__ = ("text", "squeezed", "tokens", "years")

    def __init__(self, entry: dict[str, Any]):
        self.text = f" {_norm(entry.get('text'))} {_norm(entry.get('title'))} "
        self.squeezed = self.text.replace(" ", "")
        self.tokens = set(self.text.split())
        self.years = {int(y) for y in _YEARS.findall(f"{entry.get('text') or ''} {entry.get('year') or ''}")}

    def has(self, phrase: str) -> bool:
        return bool(phrase) and f" {phrase} " in self.text


class Work:
    """A work of a list made ready for matching once: its title squeezed, its year, its first
    author's family name, volume and first page, normalised."""

    __slots__ = ("title", "words", "year", "family", "volume", "page")

    def __init__(self, title: Any, year: Any = None, family: Any = None, volume: Any = None, first_page: Any = None):
        t = _norm(title)
        self.words = len(t.split())
        self.title = t.replace(" ", "")
        y = str(year or "").strip()[:4]
        self.year = int(y) if y.isdigit() else None
        self.family, self.volume, self.page = _norm(family), _norm(volume), _norm(first_page)


def year_agrees(entry: dict[str, Any] | Entry, year: Any) -> bool:
    """Whether an entry's year can be this one: any year it prints within one of it (an article
    online in one year is often in an issue of the next), or it prints none. Any year, since the
    first four digits of an entry are as often a page ("1913-1927") or a journal's ("Periodontology
    2000") as its year."""
    e = entry if isinstance(entry, Entry) else Entry(entry)
    known = year if isinstance(year, int) else (int(str(year).strip()[:4]) if str(year or "").strip()[:4].isdigit() else None)
    return known is None or not e.years or any(abs(y - known) <= 1 for y in e.years)


def names_prepared(e: Entry, w: Work) -> bool:
    """`names`, on an entry and a work prepared once."""
    if w.words < MATCH_TITLE_WORDS or len(w.title) < MATCH_TITLE_CHARS or w.title not in e.squeezed:
        return False
    return year_agrees(e, w.year) and (not w.family or e.has(w.family))


def by_print_prepared(e: Entry, w: Work) -> bool:
    """`cites_by_place_in_print`, on an entry and a work prepared once."""
    if not w.family or not w.volume or not w.page or not e.has(w.family):
        return False
    return year_agrees(e, w.year) and w.volume in e.tokens and w.page in e.tokens


def names(entry: dict[str, Any], title: Any, year: Any = None, family: Any = None) -> bool:
    """Whether a reference entry names a work of this title, year and first author: the whole
    title inside the entry's words (at least four words and 24 letters of it, spaces and marks
    aside), a year the entry prints within one of its own (`year_agrees`), and the first author's
    family name in the entry when it is known. Anything less is no match."""
    return names_prepared(Entry(entry), Work(title, year, family))


def cites_by_place_in_print(entry: dict[str, Any], family: Any, year: Any, volume: Any, first_page: Any) -> bool:
    """Whether an entry that prints no title ("Geissler J, Stevanovic M, Injury 2019, 50, S64.")
    names a work by what it does print: the first author's family name, a year within one, and the
    volume and first page, each a whole token of the entry. All four, or no match."""
    return by_print_prepared(Entry(entry), Work(None, year, family, volume, first_page))


def first_author(w: dict[str, Any]) -> str | None:
    """An OpenAlex work's first author, as it shows the name."""
    first = next(iter(w.get("authorships") or []), None)
    return plain_text(((first or {}).get("author") or {}).get("display_name") or (first or {}).get("raw_author_name"))


def first_family(w: dict[str, Any]) -> str | None:
    """An OpenAlex work's first author's family name: the last word of the name it shows."""
    first = next(iter(w.get("authorships") or []), None)
    name = _norm(((first or {}).get("author") or {}).get("display_name") or (first or {}).get("raw_author_name"))
    return name.split()[-1] if name else None


def matches(entry: dict[str, Any], w: dict[str, Any]) -> bool:
    """Whether a reference entry names this OpenAlex work (`names`)."""
    return names(entry, w.get("title"), w.get("publication_year"), first_family(w))


def match(entry: dict[str, Any], *, timeout: float = 30, url: str | None = None) -> dict[str, Any] | None:
    """The one work an entry with no identifier names, or None: the entry's words searched, and a
    hit taken only when exactly one of them `matches` — unresolved beats misresolved."""
    words = _norm(entry.get("title")) or _norm(entry.get("text"))
    words = " ".join(w for w in words.split() if not w.isdigit() and len(w) > 1)[:300]
    if len(words.split()) < MATCH_TITLE_WORDS:
        return None
    found = [w for w in search(words, entry.get("year"), timeout=timeout, url=url) if matches(entry, w)]
    ids = {short_id(w.get("id")) for w in found}
    return found[0] if len(ids) == 1 else None
