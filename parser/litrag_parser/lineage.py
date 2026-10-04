"""Methods described elsewhere: "as previously described [14]", followed to the method it
points at.

A method is often inherited. "Collagen threads were electrocompacted as previously described
[14]" says nothing of how; the substance is in the paper the sentence cites. When the library
holds that paper, a query's hydrated method can show the cited paper's own method beside the
sentence that leans on it; when it does not, the reference is named, so a person can fetch it.
`described_elsewhere(conn, node_id)` does that for one methods node, in four steps, each a pure
read of the rows:

- **The cue.** A sentence says its method is described elsewhere, in a finite vocabulary
  (`CUES`): "as (previously) described", "described previously / elsewhere / in detail
  elsewhere", "described in detail in", "as (previously) reported", "as detailed", "a
  previously published protocol", "according to / following / followed the method
  (protocol, procedure, technique) of", "adapted / modified from". A pointer inside the
  paper ("as described above", "as described in Section 2.3"), a supplier's instructions
  ("… by the manufacturer", "… in the kit") and a figure's credit ("Figure adapted from [5]
  with permission") are not cues, nor is a finding ("previous studies reported [3]", "as
  shown in Fig. 2").
- **The reference.** The sentence is tied to the citation markers inside it, or set right
  after its stop ("described. [14] The", "described.14 The"). The markers are found in the
  paragraph the way `citations.py` found them, and only an entry the paragraph's `citations`
  rows name counts. Of the markers in the sentence, those after the cue are taken; when none
  is, the nearest one before it.
- **The paper.** Each entry is looked for among the library's papers by its DOI (case
  ignored), else its PMID, else its title — the entry's own title equal to the paper's, or,
  for an entry that is only text, the paper's title of five words or more found inside it —
  with the years agreeing when both have one, two DOIs (or PMIDs) never disagreeing, and only
  when one paper answers. An entry the library lacks may still be a candidate (`acquire.py`):
  its `cand_id` and `status` come back, so the window can offer the fetch.
- **The method.** Among the cited paper's methods subsections (the candidates of
  `edges.method_candidates`, read from its rows), the one whose own marks (`edges._ownership`,
  counted on that paper) the citing sentence and its subsection's heading name: named alone,
  or by at least two marks and twice as many as the next. Otherwise the cited paper's whole
  methods section, as `section`: the method is in there, and which part is not guessed.

Nothing here writes, calls the network or asks a model. A call reads the node and its
paragraphs (through `nodes_parent`), their `citations` and `refs` rows (by key), and — only
when a cue names an entry — the `papers` table (one row a paper) and the cited paper's methods
(through `nodes_parent` and `nodes_paper`).

    uv run --project parser python -m litrag_parser.lineage --lib DIR [--lib …] [--json]
        over every methods paragraph of each library: paragraphs with a cue, cues with a
        citation, entries found in the library (by DOI, PMID, title), methods chosen by terms
        or by section, the time a call takes, and a few examples
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .citations import _CARET, _DASHES, _IN_PAREN, _NARRATIVE, _PAREN_GROUP, _PAREN_NUM, _PARTICLES, _YEAR, _bracket_runs, _expand_numeric, _glued_markers, _norm_name
from .edges import MIN_FINDING_WORDS, _NUMBERING, _by_terms, _ownership, _terms

REF_CHARS = 300
METHOD_CHARS = 1500
SENTENCE_CHARS = 600
TITLE_WORDS_EQUAL = 3  # an entry's own title equal to a paper's: three words or more
TITLE_WORDS_INSIDE = 5  # a paper's title found inside an entry's text: five words or more
PARAGRAPH_LIKE = ("paragraph", "list_item")

# -- the cue ----------------------------------------------------------------------------------------

_NOUN = r"(?:method(?:ology)?|protocol|procedure|technique|approach)s?"
_IN_DETAIL = r"(?:in\s+(?:more\s+|further\s+|full\s+|greater\s+|great\s+)?detail\s+)?"
_STOPPED = r"(?=\s*(?:[\[(.,;]|$))"  # "described before [14]", not "described before use"
_SOURCE = r"(?:of|in|by|described|reported|published|developed|outlined)"

#: The vocabulary of a method described elsewhere, as (name, pattern), matched without regard to
#: case. It is closed on purpose: a sentence that says so in other words is missed, and is counted
#: as missed by the survey, rather than guessed at. Overlapping matches are one cue, named by the
#: first: "as described previously" is "as described".
CUES: tuple[tuple[str, str], ...] = (
    # "as described [14]", "as previously described", "as has been described", "as we have previously described"
    ("as described", r"\bas\s+(?:(?:has|have|was|were|is|are)\s+(?:been\s+)?|we\s+(?:have\s+)?)?(?:(?:previously|earlier|before|elsewhere)\s+)?described\b"),
    # "described previously", "described elsewhere", "described in detail elsewhere", "described before [14]"
    ("described previously", rf"\bdescribed\s+{_IN_DETAIL}(?:previously|elsewhere|(?:before|earlier){_STOPPED})"),
    # "described in detail in [14]", "described in more detail by Smith et al."
    ("described in detail in", r"\bdescribed\s+in\s+(?:more\s+|further\s+|full\s+|greater\s+|great\s+)?detail\s+(?:in|by)\b"),
    # "as reported [3]", "as previously reported", "as reported previously", "as we previously reported"
    ("as reported", rf"\bas\s+(?:(?:has|have|was|were)\s+(?:been\s+)?|we\s+(?:have\s+)?)?(?:(?:previously|earlier)\s+)?reported\b(?:\s+(?:previously|elsewhere|(?:before|earlier){_STOPPED}))?"),
    # "as detailed in [14]", "as previously detailed", "as detailed elsewhere"
    ("as detailed", rf"\bas\s+(?:(?:previously|earlier)\s+)?detailed\b(?:\s+(?:previously|elsewhere|(?:before|earlier){_STOPPED}))?"),
    # "a previously published protocol", "our previously described electrochemical method", "an earlier established procedure"
    ("previously published method", rf"\b(?:previously|earlier)[\s-]+(?:described|reported|published|established|developed|validated)\s+(?:[\w-]+\s+){{0,3}}?{_NOUN}\b"),
    # "according to the method of", "according to a protocol described in", "according to the procedure by"
    ("according to the method of", rf"\baccording\s+to\s+(?:the\s+|a\s+|an\s+|our\s+)?(?:[\w-]+\s+){{0,2}}?{_NOUN}\s+{_SOURCE}\b"),
    # "following the protocol of", "following a procedure described in", "we followed the method of"
    ("following the method of", rf"\bfollow(?:ing|ed)\s+(?:the\s+|a\s+|an\s+|our\s+)?(?:[\w-]+\s+){{0,2}}?{_NOUN}\s+{_SOURCE}\b"),
    # "adapted from [5]", "modified from Smith et al. (2010)"
    ("adapted from", r"\b(?:adapted|modified)\s+from\b"),
)
_CUES = [(name, re.compile(p, re.I)) for name, p in CUES]
_ANY_CUE = re.compile(r"describ|report|detail|accord|follow|adapt|modif|previous|earlier", re.I)

#: What a cue may not be followed by: a pointer inside the paper, or a supplier's instructions.
_INSIDE = re.compile(
    r"""^[\s,]*(?:
        (?:(?:in|by|under|at|to|from|for|with)\s+)?(?:the\s+|this\s+|our\s+)?(?:(?:previous|preceding|following|next|present|same|above|below|current)\s+)?
        (?:sections?\b|sect\.|subsections?\b|paragraphs?\b|chapters?\b|steps?\b|figs?\b|figures?\b|tables?\b|schemes?\b|supplement|supporting\b|appendix|appendices|methods?\s+section|equations?\b|eqs?\b|SI\b|ESI\b|manufacturer|supplier|vendor|kits?\b|instructions)
      | (?:above|below|herein|here|later|in\s+this\b|earlier\s+in\b)
    )""",
    re.I | re.X,
)
#: …nor say so before its clause ends: "as described for the controls above"
_INSIDE_CLAUSE = re.compile(r"\b(?:above|below|herein|in\s+this\s+(?:study|work|paper|article|report|section|manuscript))\b", re.I)
_CLAUSE_END = re.compile(r"[.;:(\[]")
#: a supplier's protocol is not a paper's: "according to the manufacturer's protocol"
_SUPPLIER = re.compile(r"manufacturer|supplier|vendor|\bkits?\b", re.I)
#: a figure's credit is not a method: "Figure adapted from [5]", "reprinted with permission"
_CREDIT = re.compile(r"\b(?:fig(?:ure)?s?|tables?|schem(?:e|atic)s?|images?|illustrations?|diagrams?|drawings?|photographs?|reprinted|reproduced)\b[^.;]{0,40}$", re.I)
_PERMISSION = re.compile(r"\bwith\s+permission\b|\bcopyright\b|©", re.I)

#: a stop after one of these does not end a sentence: "et al.", "Fig. 2", "St. Louis", "e.g."
_ABBREV = frozenset("al fig figs eq eqs ref refs no nos vs approx ca cf st inc co ltd corp dr prof mr ms mrs vol suppl resp sp spp var ed eds jr".split())
_STOP_CHAR = re.compile(r"[.!?]")
_WORD_BEFORE = re.compile(r"([A-Za-z]+)$")


@dataclass
class _Occ:
    """A citation marker as it sits in the paragraph, and the entries it names."""

    start: int
    end: int
    text: str
    refs: list[int]


def _texts(text: str) -> tuple[str, str]:
    """The paragraph as stored, with no-break spaces made spaces (`raw`, where carets mark a
    superscript), and the same with the carets taken out (`plain`, what is read and quoted)."""
    raw = (text or "").replace(" ", " ")
    return raw, raw.replace("^", "")


def _cue_spans(plain: str) -> list[tuple[int, int, str]]:
    """Every match of the vocabulary, overlapping matches joined into one: (start, end, name)."""
    if not _ANY_CUE.search(plain):
        return []
    hits = sorted((m.start(), m.end(), name) for name, rx in _CUES for m in rx.finditer(plain))
    out: list[list[Any]] = []
    for s, e, name in hits:
        if out and s <= out[-1][1] + 1 and not plain[out[-1][1] : s].strip():
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e, name])
    return [(s, e, name) for s, e, name in out]


def _admissible(plain: str, span: tuple[int, int], sentence: tuple[int, int], name: str) -> bool:
    """Whether a matched cue says *elsewhere*: not a pointer inside the paper, not a supplier's
    instructions, not a figure's credit."""
    s, e = span
    after = plain[e : sentence[1]]
    if _INSIDE.match(after):
        return False
    m = _CLAUSE_END.search(after)
    clause = after[: m.start()] if m else after[:80]
    if _INSIDE_CLAUSE.search(clause[:80]) or _SUPPLIER.search(plain[s:e]) or _SUPPLIER.search(clause[:80]):
        return False
    if name == "adapted from":
        if _CREDIT.search(plain[sentence[0] : s]) or _PERMISSION.search(plain[sentence[0] : sentence[1]]):
            return False
    return True


def _sentences(plain: str, occ: list[_Occ]) -> list[tuple[int, int]]:
    """The paragraph's sentences as spans. A marker set right after a stop ends the sentence it
    follows ("described. [14] The", "described.14 The"); a stop after an abbreviation, an
    initial or inside a number ends nothing."""
    ends_at: dict[int, int] = {}
    for o in occ:
        ends_at[o.start] = max(ends_at.get(o.start, o.start), o.end)
    spans: list[tuple[int, int]] = []
    begin, n = 0, len(plain)
    for m in _STOP_CHAR.finditer(plain):
        i = m.start()
        if i < begin:
            continue
        if m.group() == ".":
            w = _WORD_BEFORE.search(plain[max(0, i - 12) : i])
            if w and (len(w.group(1)) == 1 or w.group(1).lower() in _ABBREV):
                continue
        j = i + 1
        while j < n and plain[j] in ")]\"'’”":
            j += 1
        while True:  # the markers that follow the stop are the sentence's own
            k = j
            while k < n and plain[k] == " ":
                k += 1
            if k in ends_at and ends_at[k] > k:
                j = ends_at[k]
                continue
            break
        k = j
        while k < n and plain[k].isspace():
            k += 1
        if k >= n:
            spans.append((begin, n))
            begin = n
            break
        if k > j and (plain[k].isupper() or plain[k].isdigit() or plain[k] in "(["):
            spans.append((begin, j))
            begin = k
    if begin < n and plain[begin:].strip():
        spans.append((begin, n))
    return spans


def find_cues(text: str) -> list[dict[str, Any]]:
    """The cues of a passage read on its own, markers aside: `[{kind, cue, start, end}]` —
    what the vocabulary says, for a test or a person checking a sentence."""
    _, plain = _texts(text)
    sentences = _sentences(plain, [])
    out = []
    for s, e, name in _cue_spans(plain):
        sent = next(((a, b) for a, b in sentences if a <= s < b), (0, len(plain)))
        if _admissible(plain, (s, e), sent, name):
            out.append({"kind": name, "cue": re.sub(r"\s+", " ", plain[s:e]), "start": s, "end": e})
    return out


# -- the reference ----------------------------------------------------------------------------------

_DIGITS_ONLY = re.compile(rf"[\d\s,;^{_DASHES}]+")
_HAS_YEAR = re.compile(rf"\b{_YEAR}\b")


def _occurrences(raw: str, plain: str, cited: dict[int, str], refs: dict[int, Any] | None = None) -> list[_Occ]:
    """The paragraph's citation markers where they sit in `plain`, each with the entries it names
    that the paragraph's `citations` rows (`cited`: ref_no → marker as recorded) also name. The
    rows keep one marker an entry, so a second "[14]" in the paragraph, or "[14]" after an earlier
    "[12,14]", is found by reading the markers again, in the styles the rows show the paper uses."""
    out: list[_Occ] = []
    covered: set[int] = set()

    def add(start: int, end: int, nums: list[int]) -> None:
        rs = [x for x in dict.fromkeys(nums) if x in cited]
        if rs and end > start:
            out.append(_Occ(start, end, plain[start:end], rs))
            covered.update(rs)

    # brackets, a run of them one marker: "[14]", "[12,14]", "[12–14]", "[7]–[11]"
    pos = 0
    for marker, nums in _bracket_runs(plain):
        at = plain.find(marker, pos)
        if at >= 0:
            add(at, at + len(marker), nums)
            pos = at + len(marker)
    recorded = set(cited.values())
    # numbers in parentheses, where the paper cites so: "(24,25)"
    if any(_PAREN_NUM.fullmatch(m) for m in recorded):
        for m in _PAREN_NUM.finditer(plain):
            add(m.start(), m.end(), _expand_numeric(m.group(1)))
    # superscripts: a caret's ("described.^14"), or glued to the word or stop before ("described.14")
    if any(_DIGITS_ONLY.fullmatch(m) for m in recorded):
        if "^" in raw:
            shift = [0] * (len(raw) + 1)
            for i, ch in enumerate(raw):
                shift[i + 1] = shift[i] + (ch == "^")
            for m in _CARET.finditer(raw):
                add(m.start(1) - shift[m.start(1)], m.end(1) - shift[m.end(1)], _expand_numeric(m.group(1)))
        for m in _glued_markers(plain):
            add(m.start(1), m.end(1), _expand_numeric(m.group(1)))
    # author and year: "(Lyon, 2020; Kishore et al., 2012)", "Cheng et al. (2008)"
    if refs and any(_HAS_YEAR.search(m) and re.search(r"[A-Za-z]{2}", m) for m in recorded):
        keyed: dict[tuple[str, str], list[int]] = {}
        for no in cited:
            r = refs.get(no)
            if r is not None and r["first_author"] and r["year"]:
                key = _norm_name(r["first_author"])
                keyed.setdefault((key, r["year"]), []).append(no)
                keyed.setdefault((key, r["year"].rstrip("abcdefg")), []).append(no)

        def author_year(surname: str, years: str, start: int, end: int) -> None:
            words = surname.split()
            key = _norm_name(words[-1] if words[0].lower() in _PARTICLES else words[0])
            nums: list[int] = []
            for y in re.findall(_YEAR, years):
                nums.extend(keyed.get((key, y)) or keyed.get((key, y.rstrip("abcdefg"))) or [])
            add(start, end, nums)

        for g in _PAREN_GROUP.finditer(plain):
            at = g.start(1)
            for segment in g.group(1).split(";"):
                for m in _IN_PAREN.finditer(segment):
                    author_year(m.group(1), m.group(2), at + m.start(), at + m.end())
                at += len(segment) + 1
        for m in _NARRATIVE.finditer(plain):
            author_year(m.group(1), m.group(2), m.start(), m.end())
    # whatever the rows name that no reading placed: the marker as recorded, found as printed
    for no, marker in cited.items():
        if no in covered:
            continue
        for variant in dict.fromkeys((marker, marker.replace("^", ""))):
            if not variant.strip():
                continue
            for m in re.finditer(re.escape(variant), plain):
                a, b = m.start(), m.end()
                if variant[0].isdigit() and a > 0 and (plain[a - 1].isdigit() or (plain[a - 1] == "." and a > 1 and plain[a - 2].isdigit())):
                    continue
                if variant[-1].isdigit() and b < len(plain) and (plain[b].isalnum() or (plain[b] == "." and b + 1 < len(plain) and plain[b + 1].isdigit())):
                    continue
                add(a, b, [no])
    return sorted(out, key=lambda o: (o.start, o.end))


@dataclass
class Lean:
    """One entry a cue sentence leans on."""

    node_id: str
    sentence: str
    cue: str
    kind: str
    marker: str
    ref_no: int
    query: str  # the sentence without its cue and markers, after its subsection's heading: what names the cited method
    position: tuple[int, int, int]


@dataclass
class CueSentence:
    """A sentence of a paragraph that says its method is described elsewhere."""

    node_id: str
    sentence: str
    cue: str
    kind: str
    leans: list[Lean]


def read_paragraph(node_id: str, text: str, heading: str | None, cited: dict[int, str], refs: dict[int, Any] | None = None, order: int = 0) -> list[CueSentence]:
    """A paragraph's cue sentences, each with the entries it leans on: the markers after the cue
    in its sentence (those set right after its stop included), else the nearest before it. An
    entry is leaned on once a paragraph, by the first sentence that does."""
    raw, plain = _texts(text)
    spans = _cue_spans(plain)
    if not spans:
        return []
    occ = _occurrences(raw, plain, cited, refs) if cited else []
    sentences = _sentences(plain, occ)
    head = _NUMBERING.sub("", heading or "").strip()
    out: list[CueSentence] = []
    seen: set[int] = set()
    for si, (a, b) in enumerate(sentences):
        mine = [(s, e, name) for s, e, name in spans if a <= s < b and _admissible(plain, (s, e), (a, b), name)]
        if not mine:
            continue
        s, e, name = mine[0]
        inside = [o for o in occ if a <= o.start < b]
        after = [o for o in inside if o.start >= s]
        chosen = after or ([max(inside, key=lambda o: o.start)] if inside else [])
        sentence = plain[a:b].strip()
        cue = re.sub(r"\s+", " ", plain[s:e])
        rest = plain[a:s] + " " + plain[e:b]
        for o in sorted(occ, key=lambda o: -o.start):
            if a <= o.start < b:
                rest = rest.replace(o.text, " ")
        query = " ".join(x for x in (head, re.sub(r"\s+", " ", rest).strip()) if x)
        leans = []
        for oi, o in enumerate(chosen):
            for no in o.refs:
                if no in seen:
                    continue
                seen.add(no)
                leans.append(Lean(node_id, _cut(sentence, SENTENCE_CHARS), cue, name, o.text, no, query, (order, si, oi)))
        out.append(CueSentence(node_id, _cut(sentence, SENTENCE_CHARS), cue, name, leans))
    return out


# -- the paper --------------------------------------------------------------------------------------


def _rows(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row  # whatever the connection's own factory, rows by name
    return cur.execute(sql, args).fetchall()


def _cut(text: str, limit: int) -> str:
    """At most `limit` characters, cut at a word boundary."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    space = head.rfind(" ")
    return head[: space if space > limit * 0.8 else limit]


def _norm_doi(doi: str | None) -> str | None:
    d = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", (doi or "").strip(), flags=re.I).lower().rstrip(".,;)")
    return d or None


def _norm_pmid(pmid: str | None) -> str | None:
    d = re.sub(r"\D", "", pmid or "")
    return d.lstrip("0") or None


def _norm_title(title: str | None) -> str:
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", t))


def _year(y: str | None) -> str | None:
    m = re.search(r"(?:1[89]|20)\d{2}", y or "")
    return m.group(0) if m else None


def _db_file(conn: sqlite3.Connection) -> str:
    """The file behind a connection's main database; "" for one in memory or temporary."""
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main":
            return str(row[2] or "")
    return ""


@dataclass
class _Index:
    by_doi: dict[str, dict[str, Any]]
    by_pmid: dict[str, dict[str, Any]]
    titles: list[tuple[str, int, dict[str, Any]]]


#: What a call learns of a store that outlives the call, keyed by the store's file and checked
#: against the rows on every use: the papers' identifiers and normalised titles, and each cited
#: paper's methods. Both cost milliseconds to make and nothing to check, and a library's papers
#: are cited again and again — the lab's own fabrication paper by every one of its papers.
_INDEXES: dict[str, tuple[tuple[Any, ...], _Index]] = {}
_METHODS: dict[tuple[Any, ...], "_Methods | None"] = {}
_METHODS_KEPT = 64
_KEPT_LOCK = threading.Lock()  # the worker answers from more than one thread


def forget() -> None:
    """Drop what calls have kept (for a measurement that starts cold)."""
    with _KEPT_LOCK:
        _INDEXES.clear()
        _METHODS.clear()


def _papers_index(conn: sqlite3.Connection, db: str) -> _Index:
    stamp = tuple(conn.execute(
        "SELECT COUNT(*), MAX(rowid), MAX(parsed_at), MAX(added_at), TOTAL(length(title)), TOTAL(length(doi)), TOTAL(length(pmid)), TOTAL(CAST(year AS INTEGER)) FROM papers"
    ).fetchone())
    with _KEPT_LOCK:
        kept = _INDEXES.get(db) if db else None
    if kept is not None and kept[0] == stamp:
        return kept[1]
    index = _Index({}, {}, [])
    for r in _rows(conn, "SELECT key, doi, pmid, title, year FROM papers ORDER BY key"):
        row = dict(r)
        d, p = _norm_doi(row["doi"]), _norm_pmid(row["pmid"])
        if d:
            index.by_doi.setdefault(d, row)
        if p:
            index.by_pmid.setdefault(p, row)
        t = _norm_title(row["title"])
        if t:
            index.titles.append((t, len(t.split()), row))
    if db:
        with _KEPT_LOCK:
            _INDEXES[db] = (stamp, index)
    return index


class _Library:
    """The library's papers, for finding the entries a cue names: their DOIs, PMIDs and titles,
    read when a cue first names an entry and kept while the `papers` rows stay as they were."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.db = _db_file(conn)
        self._index: _Index | None = None
        self._has_candidates: bool | None = None
        self._methods: dict[str, _Methods | None] = {}

    @property
    def index(self) -> _Index:
        if self._index is None:
            self._index = _papers_index(self.conn, self.db)
        return self._index

    def resolve(self, ref: Any, citing: str) -> tuple[dict[str, Any] | None, str | None]:
        """The library's paper an entry names, and how: `doi`, `pmid` or `title`; else None."""
        doi, pmid = _norm_doi(ref["doi"]), _norm_pmid(ref["pmid"])
        index = self.index
        r = index.by_doi.get(doi) if doi else None
        if r is not None and r["key"] != citing:
            return r, "doi"
        r = index.by_pmid.get(pmid) if pmid else None
        if r is not None and r["key"] != citing:
            return r, "pmid"
        year = _year(ref["year"])
        own = _norm_title(ref["title"])
        hits = [row for t, w, row in index.titles if w >= TITLE_WORDS_EQUAL and t == own] if own else []
        if not hits:
            text = f" {_norm_title(ref['text'])} "
            hits = [row for t, w, row in index.titles if w >= TITLE_WORDS_INSIDE and f" {t} " in text]

        def agrees(row: dict[str, Any]) -> bool:
            if row["key"] == citing:
                return False
            if year and _year(row["year"]) and _year(row["year"]) != year:
                return False
            if doi and _norm_doi(row["doi"]) and _norm_doi(row["doi"]) != doi:
                return False  # two identifiers that disagree are two papers, whatever their titles
            return not (pmid and _norm_pmid(row["pmid"]) and _norm_pmid(row["pmid"]) != pmid)

        hits = [row for row in hits if agrees(row)]
        if len({row["key"] for row in hits}) == 1:
            return hits[0], "title"
        return None, None  # none, or more than one: unresolved beats misresolved

    def candidate(self, ref: Any) -> dict[str, Any] | None:
        """A candidate (`acquire.py`) with the entry's DOI, else its PMID."""
        if self._has_candidates is None:
            self._has_candidates = bool(_rows(self.conn, "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'candidates'"))
        if not self._has_candidates:
            return None
        for col, value in (("doi", _norm_doi(ref["doi"])), ("pmid", _norm_pmid(ref["pmid"]))):
            if value:
                rows = _rows(self.conn, f"SELECT cand_id, status FROM candidates WHERE {col} = ? ORDER BY cand_id LIMIT 1", (value,))
                if rows:
                    return {"cand_id": rows[0]["cand_id"], "status": rows[0]["status"]}
        return None

    def methods(self, key: str) -> _Methods | None:
        """The cited paper's methods, made from its rows, or kept from the last call while its
        rows are the ones they were made from (its reading's time and its nodes' count and last
        rowid, which a reread or a rebuild moves)."""
        if key in self._methods:
            return self._methods[key]
        stamp = None
        if self.db:
            p = self.conn.execute("SELECT parsed_at, parser, seconds FROM papers WHERE key = ?", (key,)).fetchone()
            n = self.conn.execute("SELECT COUNT(*), MAX(rowid) FROM nodes WHERE paper = ?", (key,)).fetchone()
            stamp = (self.db, key, *(tuple(p) if p else ()), *tuple(n))
        with _KEPT_LOCK:
            kept = stamp is not None and stamp in _METHODS
            m = _METHODS.get(stamp) if kept else None
        if not kept:
            m = _read_methods(self.conn, key)
            if stamp is not None:
                with _KEPT_LOCK:
                    _METHODS[stamp] = m
                    while len(_METHODS) > _METHODS_KEPT:
                        _METHODS.pop(next(iter(_METHODS)))  # the oldest made goes first
        self._methods[key] = m
        return m


# -- the method -------------------------------------------------------------------------------------


@dataclass
class _Methods:
    """A cited paper's methods, from its rows: the top sections, the candidates as
    `edges.method_candidates` makes them, the marks each owns (mark → candidate), and the
    document frequency of the owned words — all `edges._by_terms` asks of it."""

    tops: list[Any]
    cands: list[Any]
    owned: dict[str, int]
    df: dict[str, int]
    texts: dict[str, str]  # a section's paragraphs and list items, in order


_DESCENDANTS = """
WITH RECURSIVE sub(id, path) AS (
  SELECT ?, ''
  UNION ALL
  SELECT n.node_id, sub.path || printf('%06d.', n.ordinal) FROM sub CROSS JOIN nodes n ON n.parent = sub.id
)
SELECT n.node_id, n.paper, n.parent, n.type, n.heading, n.text, n.ancestry, n.page, sub.path
FROM sub CROSS JOIN nodes n ON n.node_id = sub.id
ORDER BY sub.path
"""  # CROSS JOIN keeps `sub` the outer loop, so each step probes nodes_parent and never scans nodes


def _node(r: sqlite3.Row) -> SimpleNamespace:
    return SimpleNamespace(node_id=r["node_id"], parent=r["parent"], type=r["type"], heading=r["heading"], text=r["text"] or "", ancestry=json.loads(r["ancestry"] or "[]"), page=r["page"], children=[])


def _read_methods(conn: sqlite3.Connection, key: str) -> _Methods | None:
    """The cited paper's methods candidates, as `edges.method_candidates` makes them from a tree,
    made here from its rows; None when it has no methods section."""
    tops = [_node(r) for r in _rows(conn, "SELECT node_id, parent, type, heading, text, ancestry, page FROM nodes WHERE parent = ? AND type = 'section' AND role = 'methods' ORDER BY ordinal", (key,))]
    if not tops:
        return None
    cands: list[tuple[Any, str]] = []
    texts: dict[str, str] = {}
    for top in tops:
        nodes = [_node(r) for r in _rows(conn, _DESCENDANTS, (top.node_id,))]
        by_id = {n.node_id: n for n in nodes}
        for n in nodes[1:]:
            if n.parent in by_id:
                by_id[n.parent].children.append(n)
        top = by_id[top.node_id]

        def under(n: Any) -> list[Any]:
            out, stack = [], [n]
            while stack:
                x = stack.pop()
                out.append(x)
                stack.extend(reversed(x.children))
            return out

        for n in nodes:
            if n.type == "section":
                texts[n.node_id] = " ".join(x.text for x in under(n) if x.type in PARAGRAPH_LIKE and x.text)
        subs = [c for c in top.children if c.type == "section"]
        if subs:
            words_before = sum(len(c.text.split()) for c in top.children[: top.children.index(subs[0])] if c.type == "paragraph")
            words_all = sum(len(x.text.split()) for x in under(top) if x.type == "paragraph" and x.text)
            seen_sub = words_all > 0 and words_before >= 0.4 * words_all
            for c in top.children:
                if c.type == "section":
                    seen_sub = True
                    cands.append((c, " ".join([c.heading or "", *(x.text for x in under(c) if x.type in ("paragraph", "list_item", "caption") and x.text)])))
                elif seen_sub and c.type == "paragraph" and len(c.text.split()) >= MIN_FINDING_WORDS:
                    cands.append((c, c.text))
        else:
            cands.extend((p, p.text) for p in under(top) if p.type == "paragraph" and len(p.text.split()) >= MIN_FINDING_WORDS)
    df: Counter[str] = Counter()
    for r in _rows(conn, "SELECT text FROM nodes WHERE paper = ? AND type IN ('paragraph', 'list_item', 'caption') AND text != ''", (key,)):
        df.update(_terms(r["text"]))
    owned = _ownership(cands, dict(df)) if len(cands) > 1 else {}
    return _Methods(tops=tops, cands=[n for n, _ in cands], owned=owned, df={t: df[t] for t in owned if " " not in t}, texts=texts)


def _method_of(m: _Methods, n: Any, evidence: str, detail: str) -> dict[str, Any]:
    if n.type == "section":
        heading, ancestry, text = n.heading, [*n.ancestry, n.heading] if n.heading else list(n.ancestry), m.texts.get(n.node_id, "")
    else:
        heading, ancestry, text = (n.ancestry[-1] if n.ancestry else None), list(n.ancestry), n.text
    return {"node_id": n.node_id, "heading": heading, "ancestry": ancestry, "page": n.page, "text": _cut(text, METHOD_CHARS), "evidence": evidence, "detail": detail}


def choose_method(m: _Methods, query: str) -> dict[str, Any]:
    """The cited paper's method the query names by marks only one of its subsections owns —
    named alone, or by at least two marks and twice as many as the next — else its whole
    methods section (`section`), the method being somewhere in it."""
    hits = sorted(_by_terms(query, m.owned, m.df), key=lambda h: (-len(h[1]), h[0])) if m.owned else []
    if hits and (len(hits) == 1 or (len(hits[0][1]) >= 2 and len(hits[0][1]) >= 2 * len(hits[1][1]))):
        i, ts = hits[0]
        return _method_of(m, m.cands[i], "terms", ", ".join(ts[:4]))
    if hits:
        detail = "no clear winner: " + "; ".join(f"{(m.cands[i].heading or m.cands[i].text[:40])} ({', '.join(ts[:3])})" for i, ts in hits[:3])
    elif len(m.cands) < 2:
        detail = "one methods section, no subsections to choose among"
    else:
        detail = "the citing sentence names no subsection's own terms"
    if len(m.tops) > 1:
        detail += f"; the first of {len(m.tops)} methods sections"
    return _method_of(m, m.tops[0], "section", detail)


# -- the call ---------------------------------------------------------------------------------------


def _paragraphs(conn: sqlite3.Connection, node_id: str) -> list[sqlite3.Row]:
    r = _rows(conn, "SELECT node_id, paper, type, text, ancestry FROM nodes WHERE node_id = ?", (node_id,))
    if not r:
        return []
    if r[0]["type"] in PARAGRAPH_LIKE:
        return r
    if r[0]["type"] != "section":
        return []
    return [x for x in _rows(conn, _DESCENDANTS, (node_id,)) if x["type"] in PARAGRAPH_LIKE and x["text"]]


def _leans(conn: sqlite3.Connection, paragraphs: list[sqlite3.Row], paper: str) -> tuple[list[CueSentence], dict[int, sqlite3.Row]]:
    """The cue sentences of some paragraphs of one paper, and the entries they cite, by number."""
    sentences: list[CueSentence] = []
    refs: dict[int, sqlite3.Row] = {}
    for order, p in enumerate(paragraphs):
        if not _cue_spans(_texts(p["text"])[1]):
            continue  # most paragraphs end here, with nothing read but their text
        cited = {r["ref_no"]: r["marker"] for r in _rows(conn, "SELECT ref_no, marker FROM citations WHERE node_id = ? ORDER BY ref_no", (p["node_id"],))}
        mine: dict[int, sqlite3.Row] = {}
        if cited:
            nums = sorted(cited)
            mine = {r["ref_no"]: r for r in _rows(conn, f"SELECT ref_no, text, doi, pmid, year, first_author, title FROM refs WHERE paper = ? AND ref_no IN ({', '.join('?' * len(nums))})", (paper, *nums))}
            refs.update(mine)
        ancestry = json.loads(p["ancestry"] or "[]")
        # the subsection's heading says what the method is ("2.2 Preparation of ELAC threads"); the
        # methods section's own ("2. Materials and Methods") says nothing, and is left out
        sentences.extend(read_paragraph(p["node_id"], p["text"], ancestry[-1] if len(ancestry) >= 2 else None, cited, mine, order))
    return sentences, refs


def _answer(lib: _Library, lean: Lean, ref: sqlite3.Row | None, citing: str) -> dict[str, Any]:
    out: dict[str, Any] = {"node_id": lean.node_id, "sentence": lean.sentence, "cue": lean.cue, "marker": lean.marker, "ref_no": lean.ref_no, "ref": None, "paper": None, "method": None, "candidate": None}
    if ref is None:
        return out
    out["ref"] = {"text": _cut(ref["text"] or "", REF_CHARS), "doi": ref["doi"], "pmid": ref["pmid"], "year": ref["year"], "first_author": ref["first_author"], "title": ref["title"]}
    paper, by = lib.resolve(ref, citing)
    if paper is not None:
        out["paper"] = {"key": paper["key"], "title": paper["title"], "year": paper["year"], "by": by}
        m = lib.methods(paper["key"])
        if m is not None:
            out["method"] = choose_method(m, lean.query)
    out["candidate"] = lib.candidate(ref)
    return out


def described_elsewhere(conn: sqlite3.Connection, node_id: str, limit: int = 3) -> list[dict[str, Any]]:
    """For a methods node (a subsection, or a paragraph or list item), the entries its sentences
    say the method is described in, each followed into the library when it is there:

        [{node_id, sentence, cue, marker, ref_no,
          ref: {text, doi, pmid, year, first_author, title},
          paper: {key, title, year, by} | None,
          method: {node_id, heading, ancestry, page, text, evidence: terms | section, detail} | None,
          candidate: {cand_id, status} | None}]

    at most `limit`, those whose cited method was found first, then those whose paper was, then
    the rest, each group in reading order; an entry once. `node_id` is the citing paragraph and
    `paper.by` says how the entry was found (`doi`, `pmid`, `title`). A node with no cue, or a
    cue with no marker, gives []."""
    paragraphs = _paragraphs(conn, node_id)
    if not paragraphs:
        return []
    paper = paragraphs[0]["paper"]
    sentences, refs = _leans(conn, paragraphs, paper)
    leans = [l for s in sentences for l in s.leans]
    seen: set[int] = set()
    unique = []
    for l in sorted(leans, key=lambda l: l.position):
        if l.ref_no not in seen:
            seen.add(l.ref_no)
            unique.append(l)
    if not unique:
        return []
    lib = _Library(conn)
    out = [_answer(lib, l, refs.get(l.ref_no), paper) for l in unique]
    out.sort(key=lambda a: 0 if a["method"] else 1 if a["paper"] else 2)
    return out[: max(0, limit)]


# -- the survey -------------------------------------------------------------------------------------


def survey(conn: sqlite3.Connection, examples: int = 6) -> dict[str, Any]:
    """Over every methods paragraph of a store: how often a sentence says its method is described
    elsewhere, how often that sentence cites, how often the entry is in the library and how,
    how its method was chosen; and the time `described_elsewhere` takes on every methods node."""
    paras = _rows(conn, "SELECT node_id, paper, type, text, ancestry FROM nodes WHERE role = 'methods' AND type IN ('paragraph', 'list_item') AND text != '' ORDER BY paper, node_id")
    by_paper: dict[str, list[sqlite3.Row]] = {}
    for p in paras:
        by_paper.setdefault(p["paper"], []).append(p)
    c: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    found: list[dict[str, Any]] = []
    lib = _Library(conn)
    for paper, ps in by_paper.items():
        sentences, refs = _leans(conn, ps, paper)
        c["paragraphs_with_cue"] += len({s.node_id for s in sentences})
        c["cue_sentences"] += len(sentences)
        for s in sentences:
            kinds[s.kind] += 1
            c["cue_sentences_cited" if s.leans else "cue_sentences_uncited"] += 1
            for lean in s.leans:
                a = _answer(lib, lean, refs.get(lean.ref_no), paper)
                c["entries"] += 1
                if a["paper"]:
                    c[f"resolved_{a['paper']['by']}"] += 1
                    c["resolved"] += 1
                    c[f"method_{a['method']['evidence']}" if a["method"] else "method_none"] += 1
                else:
                    c["unresolved"] += 1
                if a["candidate"]:
                    statuses[a["candidate"]["status"]] += 1
                found.append({**a, "citing": paper})
    found.sort(key=lambda a: 0 if a["method"] and a["method"]["evidence"] == "terms" else 1 if a["method"] else 2 if a["paper"] else 3)

    def pick(pool: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
        out, seen = [], set()
        for a in pool:
            if len(out) >= n:
                break
            if (a["citing"], a["sentence"], bool(a["paper"])) not in seen:
                seen.add((a["citing"], a["sentence"], bool(a["paper"])))
                out.append(a)
        return out

    shown = pick([a for a in found if a["paper"]], max(0, examples - 2))
    shown += pick([a for a in found if not a["paper"]], max(0, examples - len(shown)))
    # the time a call takes, on every node it may be asked about: a methods subsection or paragraph
    # and kept nothing from before, so each cited paper's first call pays for reading its methods
    nodes = [r["node_id"] for r in _rows(conn, "SELECT node_id FROM nodes WHERE role = 'methods' AND (type IN ('paragraph', 'list_item') OR (type = 'section' AND parent NOT IN (SELECT key FROM papers)))")]
    forget()
    times, answered = [], []
    for nid in nodes:
        t0 = time.perf_counter()
        got = described_elsewhere(conn, nid)
        ms = (time.perf_counter() - t0) * 1000
        times.append(ms)
        if got:
            answered.append(ms)
    times.sort()
    answered.sort()
    return {
        "papers": int(_rows(conn, "SELECT COUNT(*) AS n FROM papers")[0]["n"]),
        "papers_with_methods": len(by_paper),
        "nodes": int(_rows(conn, "SELECT COUNT(*) AS n FROM nodes")[0]["n"]),
        "methods_paragraphs": len(paras),
        **{k: c.get(k, 0) for k in ("paragraphs_with_cue", "cue_sentences", "cue_sentences_cited", "cue_sentences_uncited", "entries", "resolved", "resolved_doi", "resolved_pmid", "resolved_title", "unresolved", "method_terms", "method_section", "method_none")},
        "cues": dict(kinds.most_common()),
        "candidates": dict(statuses.most_common()),
        "timing": {**_spread(times), "answered": _spread(answered)},
        "examples": shown,
    }


def _spread(ms: list[float]) -> dict[str, Any]:
    """Count, mean, 95th percentile and maximum of sorted times in milliseconds."""
    if not ms:
        return {"calls": 0, "mean_ms": None, "p95_ms": None, "max_ms": None}
    return {"calls": len(ms), "mean_ms": round(sum(ms) / len(ms), 3), "p95_ms": round(ms[int(0.95 * (len(ms) - 1))], 3), "max_ms": round(ms[-1], 3)}


def _open_ro(store: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{store.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _print(name: str, s: dict[str, Any]) -> None:
    t = s["timing"]
    print(f"{name}: {s['papers']} papers ({s['papers_with_methods']} with methods paragraphs) · {s['nodes']} nodes · {s['methods_paragraphs']} methods paragraphs")
    print(f"  paragraphs with a cue       {s['paragraphs_with_cue']:>5} · cue sentences {s['cue_sentences']} ({', '.join(f'{k} {v}' for k, v in s['cues'].items()) or 'none'})")
    print(f"  cues with a citation        {s['cue_sentences_cited']:>5} · without {s['cue_sentences_uncited']}")
    print(f"  entries leaned on           {s['entries']:>5}")
    print(f"  found in the library        {s['resolved']:>5} (by DOI {s['resolved_doi']} · PMID {s['resolved_pmid']} · title {s['resolved_title']}) · not found {s['unresolved']}")
    print(f"  method chosen               terms {s['method_terms']} · section {s['method_section']} · none (no methods section) {s['method_none']}")
    if s["candidates"]:
        print(f"  entries that are candidates {sum(s['candidates'].values()):>5} ({', '.join(f'{k} {v}' for k, v in s['candidates'].items())})")
    if t["calls"]:
        print(f"  described_elsewhere         {t['calls']} methods nodes · mean {t['mean_ms']} ms · p95 {t['p95_ms']} ms · max {t['max_ms']} ms")
        a = t["answered"]
        if a["calls"]:
            print(f"    those with an answer      {a['calls']} · mean {a['mean_ms']} ms · p95 {a['p95_ms']} ms · max {a['max_ms']} ms (cold: nothing kept from before)")
    for a in s["examples"]:
        ref = a["ref"] or {}
        print(f"\n  [{a['citing']}] {a['sentence'][:220]}")
        print(f"    cue {a['cue']!r} · marker {a['marker']} → entry {a['ref_no']}: {(ref.get('first_author') or '')} {(ref.get('year') or '')} · {(ref.get('title') or ref.get('text') or '')[:110]}")
        if a["paper"]:
            print(f"    in the library by {a['paper']['by']}: {a['paper']['key']} · {(a['paper']['title'] or '')[:90]}")
            m = a["method"]
            if m:
                print(f"    method ({m['evidence']}: {m['detail'][:90]}): {' > '.join(m['ancestry'])[:100]} · p. {m['page']}")
                print(f"      {m['text'][:200]}")
        else:
            cand = a["candidate"]
            print("    not in the library" + (f" · candidate {cand['cand_id']} ({cand['status']})" if cand else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="litrag_parser.lineage", description=__doc__.split("\n\n")[0])
    ap.add_argument("--lib", action="append", default=[], help="a library directory (repeatable)")
    ap.add_argument("--json", action="store_true", help="print the survey as JSON")
    ap.add_argument("--examples", type=int, default=6, help="how many examples to show a library")
    args = ap.parse_args(argv)
    if not args.lib:
        ap.print_help()
        return 2
    report: dict[str, Any] = {}
    for lib in args.lib:
        d = Path(lib).expanduser()
        store = d / "store.sqlite"
        if not store.exists():
            print(f"{d}: no store.sqlite", file=sys.stderr)
            return 1
        conn = _open_ro(store)
        try:
            report[str(d)] = survey(conn, examples=args.examples)
        finally:
            conn.close()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    for name, s in report.items():
        _print(name, s)
        print()
    if len(report) > 1:
        keys = ("methods_paragraphs", "paragraphs_with_cue", "cue_sentences", "cue_sentences_cited", "entries", "resolved", "resolved_doi", "resolved_pmid", "resolved_title", "method_terms", "method_section")
        print("all: " + " · ".join(f"{k} {sum(s[k] for s in report.values())}" for k in keys))
    return 0


if __name__ == "__main__":
    sys.exit(main())
