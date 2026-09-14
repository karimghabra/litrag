"""Who wrote the paper, where it appeared, and when — from the data the reader already has.

A JATS file states its authors and their affiliations in `<contrib-group>`; Europe PMC's
record for a DOI or PMID carries the author string, the journal and the year, and its
publication types (paper_type.py). Both are taken as stated, never inferred: for a PDF
with no record, the front matter's `authors` and `affiliations` lines stay what the
window shows, and the columns stay empty. Fetched once at ingest (unless `offline`),
stored on the paper (`papers.authors`, `journal`, `year`, `pub_types`); the file's word
is read again on every rebuild and overrides the record's.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any

_GROUP = re.compile(r"<contrib-group\b([^>]*)>(.*?)</contrib-group>", re.S | re.I)
_CONTRIB = re.compile(r"<contrib\b([^>]*)>(.*?)</contrib>", re.S | re.I)
_TYPE = re.compile(r"(?:contrib|content)-type=[\"']([^\"']+)[\"']", re.I)
_SURNAME = re.compile(r"<surname>(.*?)</surname>", re.S)
_GIVEN = re.compile(r"<given-names[^>]*>(.*?)</given-names>", re.S)
_STRING_NAME = re.compile(r"<string-name[^>]*>(.*?)</string-name>", re.S)
_COLLAB = re.compile(r"<collab[^>]*>(.*?)</collab>", re.S)
_AFF_XREF = re.compile(r"<xref[^>]*ref-type=[\"']aff[\"'][^>]*rid=[\"']([^\"']+)[\"']", re.I)
_AFF = re.compile(r"<aff\b[^>]*\bid=[\"']([^\"']+)[\"'][^>]*>(.*?)</aff>", re.S | re.I)
_AFF_ANY = re.compile(r"<aff\b[^>]*>(.*?)</aff>", re.S | re.I)
_CORRESP = re.compile(r"corresp=[\"']yes[\"']|ref-type=[\"']corresp[\"']", re.I)
_LABEL = re.compile(r"<label>.*?</label>", re.S)
_TAG = re.compile(r"<[^>]+>")
_JOURNAL = re.compile(r"<journal-title>(.*?)</journal-title>", re.S)
_YEAR = re.compile(r"<pub-date\b[^>]*>.*?<year>(\d{4})</year>", re.S)


def _text(markup: str) -> str:
    t = _TAG.sub(" ", markup)
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return " ".join(t.split())


def _name(body: str) -> str | None:
    surname, given, string_name, collab = _SURNAME.search(body), _GIVEN.search(body), _STRING_NAME.search(body), _COLLAB.search(body)
    if surname:
        return " ".join(x for x in (_text(given.group(1)) if given else "", _text(surname.group(1))) if x)
    if string_name:
        return _text(string_name.group(1)) or None
    if collab:
        return _text(collab.group(1)) or None
    return None


def jats_authors(xml: bytes | None) -> list[dict[str, Any]]:
    """`[{name, affiliations, corresponding}]` from a JATS file's contributor groups — the
    authors, not the editors — with each affiliation resolved through its `rid`; empty when
    the file names none."""
    if not xml:
        return []
    head = xml[:300000].decode("utf-8", "replace")
    affs = {aid: _text(_LABEL.sub("", body)) for aid, body in _AFF.findall(head)}
    out: list[dict[str, Any]] = []
    for group_attrs, group in _GROUP.findall(head):
        kind = _TYPE.search(group_attrs)
        if kind and kind.group(1).lower() not in ("author", "authors"):
            continue  # editors, reviewers
        for attrs, body in _CONTRIB.findall(group):
            kind = _TYPE.search(attrs)
            if kind and kind.group(1).lower() != "author":
                continue
            name = _name(body)
            if not name:
                continue
            aff_ids = _AFF_XREF.findall(body)
            affiliations = [affs[a] for a in aff_ids if a in affs] or [_text(_LABEL.sub("", a)) for a in _AFF_ANY.findall(body)]
            out.append({"name": name, "affiliations": affiliations, "corresponding": bool(_CORRESP.search(attrs) or _CORRESP.search(body))})
    return out


def jats_journal(xml: bytes | None) -> tuple[str | None, str | None]:
    """The journal's title and the year of the first publication date the file states."""
    if not xml:
        return None, None
    head = xml[:100000].decode("utf-8", "replace")
    j, y = _JOURNAL.search(head), _YEAR.search(head)
    return (_text(j.group(1)) or None if j else None), (y.group(1) if y else None)


def lookup_record(doi: str | None = None, pmid: str | None = None, timeout: float = 6.0) -> dict[str, Any] | None:
    """Europe PMC's record for one paper, by DOI else PMID: `{pub_types, authors, journal,
    year}` — the authors as the record's author string gives them, surname and initials,
    with no affiliations. None offline or when the record is unknown."""
    if not doi and not pmid:
        return None
    query = f'DOI:"{doi}"' if doi else f"EXT_ID:{pmid} AND SRC:MED"
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={urllib.parse.quote(query)}&format=json&resultType=lite&pageSize=1"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    hits = (data.get("resultList") or {}).get("result") or []
    if not hits:
        return None
    h = hits[0]
    authors = [{"name": a.strip().rstrip("."), "affiliations": [], "corresponding": False} for a in (h.get("authorString") or "").split(",") if a.strip()]
    return {
        "pub_types": [p.strip() for p in (h.get("pubType") or "").split(";") if p.strip()],
        "authors": authors,
        "journal": h.get("journalTitle") or None,
        "year": str(h.get("pubYear")) if h.get("pubYear") else None,
    }
