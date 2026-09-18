"""In-text citations, linked to the reference list.

A paper's reference list sits at its end as one node per entry; its sentences cite those
entries inline — "[6,9,12]" in a numbered journal, "(Lyon, 2020; Fields and Levin 2022)"
or "Levin (2019)" in an author–year one. Every style a publisher sets a number in is read
the same way: inside brackets, one bracket to an entry ("[7]–[11]", "[12],14,21,[40]"),
raised on the page ("tendons^2,^3", "topography^7–11", "energy.^3−9"), or in parentheses
("(1, 2)", "(3–5)"). Which of them a paper uses is decided from the paper itself, because
the same digits mean other things: "BaTiO3" is a formula, "−20 °C" a temperature, ".05" a
p-value, "^2Oceans Institute" an affiliation, and none of them cites anything. Markers are
found in each node's text and tied to the entry they name, so a node knows which papers it
leans on and an entry knows which nodes lean on it: rows in `refs` and `citations`. A JATS
file names its entries outright (`<ref id>` with DOI and PMID), so those enrich the rows;
a PDF gives only the entry's text, from which a first author, a year and a DOI are read.

Unlinkable beats mislinked: a marker that names no entry is left alone, and an
author–year that fits two entries (2022a and 2022b, cited as 2022) links both.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .tree import Node, Tree

_YEAR = r"(?:1[89]|20)\d{2}[a-z]?"
_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")
_YEAR_RE = re.compile(rf"\b({_YEAR})\b")
#: every dash a publisher sets a range of entries with. ACS writes "3−9" with a minus sign, others
#: with a hyphen, a figure dash or an en dash, and the PDF's font may hand over any of them.
_DASHES = "‐‑‒–—―−-"
_DASH = f"[{_DASHES}]"
#: inside a bracket a range may also arrive as " e ": Elsevier's en dash comes out of some of their
#: PDFs as the letter e, so "[1 e 3]" is "[1–3]" and "[12 e 14]" is "[12–14]"
_IN_BRACKET = rf"(?:\s*{_DASH}\s*|\s+e\s+)"
_NUMERIC = re.compile(rf"\[(\d{{1,3}}(?:{_IN_BRACKET}\d{{1,3}})?(?:\s*,\s*\d{{1,3}}(?:{_IN_BRACKET}\d{{1,3}})?)*)\]")
# a superscript the layout model marked as one, caret and all: "tendons^2,^3", "topography^7–11",
# "energy.^3−9", "2024.^1,^2 In". The caret says outright that the number was raised on the page, so
# a word of any shape may sit before it — what must not is a digit ("10^6"), and what must not follow
# is a letter ("^2Oceans Institute", an affiliation).
_CARET = re.compile(rf"(?:[A-Za-z]{{3,}}|[.,;:)\]?!%’”'\"])\s?\^\s?(\d{{1,3}}(?:\s?[,;]\s?\^?\s?\d{{1,3}}|\s?{_DASH}\s?\^?\s?\d{{1,3}})*)(?![A-Za-z0-9])")
# a superscript citation the layout model glued to the word before it: "injury.1 Worldwide", "tendons2,3 and"
_GLUED = re.compile(rf"(?:[A-Za-z]{{3,}}|(?<!\d)[.,;:)\]])\^?(\d{{1,3}}(?:\s?[,{_DASHES}]\s?\d{{1,3}})*)(?=[\s.,;:)\]]|$)")  # not "86.4", not "3.1 mg"; "al.^29" as well as "al.29"
# the same superscript with the layout model's space before it, after a stop and before a capital:
# "applications. 17 While", "medicine. 29,30 The", "requirements. 10 - 12 In". Measured: reading such
# a number after a plain word as well ("incubated 24 h", "in 5 patients") costs more than it wins.
_SPACED = re.compile(rf"(?<=[.,;:)\]]) (\d{{1,3}}(?:\s?[,{_DASHES}]\s?\d{{1,3}})*)(?= [A-Z(]|$)")
# numbers in parentheses — "(1)", "(1, 2)", "(3–5)" — the style of Frontiers, Science and NAR, read
# only when the paper brackets nothing and most of them name an entry. A single letter in front of
# one makes it a statistic's parameters, not a citation: "F (1, 13) = 0.024, p = 0.88".
_PAREN_NUM = re.compile(rf"(?<![A-Za-z\d.])(?<!\b[A-Za-z] )\((\d{{1,3}}(?:\s*{_DASH}\s*\d{{1,3}})?(?:\s*,\s*\d{{1,3}}(?:\s*{_DASH}\s*\d{{1,3}})?)*)\)(?![A-Za-z\d:])")  # not "(1): the preAV group", a sentence numbering its own points
_NAME = r"[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+)?"
_AUTHORS = rf"({_NAME})(?:\s+et\s+al\.?|\s+(?:and|&)\s+{_NAME})?"
_PAREN_GROUP = re.compile(rf"\(([^()]*?{_YEAR}[^()]*)\)")
_IN_PAREN = re.compile(rf"{_AUTHORS}\s*,?\s*({_YEAR}(?:\s*,\s*{_YEAR})*)")
_NARRATIVE = re.compile(rf"{_AUTHORS}\s*\(\s*({_YEAR}(?:\s*,\s*{_YEAR})*)\s*\)")
_PARTICLES = {"de", "van", "der", "von", "da", "di", "le", "la", "del", "den", "ten", "ter", "du", "dos", "das", "el", "al"}
_CITING_TYPES = {"paragraph", "list_item", "caption", "footnote"}


@dataclass
class Ref:
    ref_no: int
    node_id: str | None
    text: str
    ref_id: str | None = None
    doi: str | None = None
    pmid: str | None = None
    year: str | None = None
    first_author: str | None = None
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Citation:
    node_id: str
    ref_no: int
    marker: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if ch.isalpha()).lower()


def first_surname(entry: str) -> str | None:
    """The first author's surname at the head of a reference entry: "Adams, F. (2018)",
    "Wang Y., Wang Z.", "Parenteau-Bareil R.", "van der Berg, J."."""
    words = entry.replace(" ", " ").split()
    out: list[str] = []
    for w in words[:4]:
        bare = w.strip(",;:.()")
        if not bare:
            break
        if len(bare) == 1 or (len(bare) <= 3 and bare.isupper()) or re.fullmatch(r"(?:[A-Z]\.)+[A-Z]?\.?", bare):
            break  # an initial, or initials
        if bare.lower() in _PARTICLES or bare[0].isupper():
            out.append(bare)
        else:
            break
        if w.endswith(","):
            break
    return " ".join(out) or None


#: where an entry begins in a reference list that prints its numbers: "12. Adams F.", "[12] Adams F."
_ENTRY_START = re.compile(r"(?:(?<=[.;\])]\s)|^)\[?(\d{1,3})[\].]\s+(?=[A-Z\[])")


def _note_split(tree: Tree, node: Node, before: str, after: str) -> None:
    """Say in the review that an entry was cut out of a node that held two. The linker runs over a
    tree more than once in a rebuild, so a change already recorded is not recorded again."""
    repairs = getattr(tree, "repairs", None)
    log = getattr(repairs, "log", None)
    if log is None:
        return
    if any(r.get("kind") == "split_references" and r.get("node_id") == node.node_id for r in log):
        return
    repairs.note(
        "split_references",
        page=node.page,
        node_id=node.node_id,
        before=before,
        after=after,
        why="split_references: the reference list ran two entries together in one block, and the second one's printed number says where the first ends.",
    )


def _split_entries(nodes: list[Node], tree: Tree | None = None) -> list[tuple[Node, str]]:
    """A reference list whose entries the layout model ran together — "3. World Health Organisation
    … 4. Food and Agriculture Organization …" in one paragraph — cut back apart at the numbers it
    prints. An entry's own text carries numbers too (a volume, a page, a year), so a cut is made
    only where the number is the one the list is up to."""
    out: list[tuple[Node, str]] = []
    expect: int | None = None
    for n in nodes:
        text = re.sub(r"\s+", " ", n.text).strip()
        last = 0
        for m in _ENTRY_START.finditer(text):
            num = int(m.group(1))
            if m.start() == 0:
                expect = num
                continue
            piece = text[last : m.start()].strip()
            # a page number reads like the next entry's — "Biomedical Microdevices 2017, 19, 72.
            # [CrossRef]" — so both sides of a cut have to be long enough to be entries of their own
            if expect is None or num != expect + 1 or len(piece) < 30 or len(text) - m.start() < 30:
                continue
            out.append((n, piece))
            if tree is not None:
                _note_split(tree, n, text, piece)
            last, expect = m.start(), num
        rest = text[last:].strip()
        if rest:
            out.append((n, rest))
    return out


def reference_entries(tree: Tree) -> list[Ref]:
    """One Ref per entry in the references lane, numbered in order."""
    nodes = [n for n in tree.walk() if n.role == "references" and n.type in ("list_item", "paragraph") and n.text.strip()]
    entries = _split_entries(nodes, tree)
    refs: list[Ref] = []
    printed: list[int | None] = []
    for i, (n, text) in enumerate(entries):
        m = re.match(r"^\[?(\d{1,3})[\].]\s+", text)
        printed.append(int(m.group(1)) if m else None)
        text = re.sub(r"^\[?(\d{1,3})[\].]\s+", "", text)  # an entry's own number, if printed
        doi = _DOI.search(text)
        year = _YEAR_RE.search(text)
        refs.append(Ref(ref_no=i + 1, node_id=n.node_id, text=text, doi=doi.group(0).rstrip(".,;)") if doi else None, year=year.group(1) if year else None, first_author=first_surname(text)))
    # when the paper prints its numbers and they run in order, they are the entry numbers: a
    # merged or a missing entry no longer shifts every link after it; an unnumbered entry in
    # such a list is the rest of the one before it, and links nowhere
    nums = [p for p in printed if p is not None]
    if refs and len(nums) >= 0.8 * len(refs) and nums == sorted(nums) and len(set(nums)) == len(nums) and nums[0] <= 2 and nums[-1] > len(nums) * 0.5:
        numbered: list[Ref] = []
        for r, p in zip(refs, printed):
            if p is None:
                continue
            r.ref_no = p
            numbered.append(r)
        return numbered
    return refs


def enrich_from_jats(refs: list[Ref], xml: bytes) -> list[Ref]:
    """The JATS `<ref-list>`: id, DOI, PMID, year, first surname and title per entry, aligned
    to the tree's entries by order when the counts agree, else by DOI or author and year."""
    from lxml import etree

    try:
        root = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=True, recover=True))
    except etree.XMLSyntaxError:
        return refs
    if root is None:
        return refs
    found: list[dict[str, Any]] = []
    for ref in root.iter("ref"):
        def one(xpath: str) -> str | None:
            el = ref.find(xpath)
            return (el.text or "").strip() if el is not None and el.text else None

        doi = one(".//pub-id[@pub-id-type='doi']") or (ref.find(".//ext-link[@ext-link-type='doi']").get("{http://www.w3.org/1999/xlink}href") if ref.find(".//ext-link[@ext-link-type='doi']") is not None else None)
        pmid = one(".//pub-id[@pub-id-type='pmid']") or (ref.find(".//ext-link[@ext-link-type='pmid']").get("{http://www.w3.org/1999/xlink}href") if ref.find(".//ext-link[@ext-link-type='pmid']") is not None else None)
        label = one("label")
        found.append({"ref_id": ref.get("id"), "doi": doi, "pmid": pmid, "year": one(".//year"), "surname": one(".//surname"), "title": one(".//article-title") or one(".//chapter-title") or one(".//source"), "label": int(label.rstrip(".")) if label and label.rstrip(".").isdigit() else None})
    if not found:
        return refs
    by_doi = {f["doi"].lower(): f for f in found if f.get("doi")}
    by_label = {f["label"]: f for f in found if f.get("label") is not None}
    aligned = len(found) == len(refs)
    for i, r in enumerate(refs):
        f = None
        if r.doi and r.doi.lower() in by_doi:
            f = by_doi[r.doi.lower()]
        elif r.ref_no in by_label:
            f = by_label[r.ref_no]
        elif aligned:
            f = found[i]
        elif not by_label and 1 <= r.ref_no <= len(found):
            f = found[r.ref_no - 1]
        if f is None:
            continue
        r.ref_id = f["ref_id"] or r.ref_id
        r.doi = f["doi"] or r.doi
        r.pmid = f["pmid"] or r.pmid
        r.year = f["year"] or r.year
        r.first_author = f["surname"] or r.first_author
        r.title = f["title"] or r.title
    return refs


_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*$")


def _is_word(before: str) -> bool:
    """Whether the text ends in a word a superscript could follow.

    A formula, a gene or a tool carries its number inside its name — "BaTiO3", "SiO2", "H2O2",
    "UniRef90", "MMseqs2" — and reading that number as a citation is how a chemistry paper came
    to cite a hundred entries it never named. Such a name is told from a word by its case: a word
    is "tendons" or "Butyrate" — a capital inside it, or a digit, and the number at its end
    belongs to the name. Measured: letting acronyms through too ("PSCs", "GWAS") costs more in
    formulas read as citations than it wins back in markers."""
    m = _TOKEN.search(before)
    if m is None:
        return True  # the match sits on punctuation, which the pattern has already vouched for
    token = m.group(0)
    return not any(ch.isdigit() for ch in token) and not any(ch.isupper() for ch in token[1:])


#: what a number belongs to when it is not raised at all: "Fig. 1. Map and climatology", "Table 2",
#: "Equation 3". A caption opens with one and the glued reading took it for a superscript.
_NUMBERED_THING = re.compile(r"\b(?:Fig|Figs|Figure|Figures|Table|Tables|Eq|Eqs|Equation|Scheme|Movie|Video|Panel|Note|Notes|Section|Chapter|Step|Ref|Refs)\.?\s*$")


def _glued_markers(text: str) -> Iterable[re.Match[str]]:
    """The superscripts a paper printed without a caret, formulas and figure numbers left out."""
    for m in list(_GLUED.finditer(text)) + list(_SPACED.finditer(text)):
        before = text[: m.start(1)]
        if _is_word(before.rstrip()) and not _NUMBERED_THING.search(before.rstrip(". ")):
            yield m


def _front(node: Node) -> bool:
    """Whether a node is part of the paper's front matter — the authors, their affiliations, the
    running head — where a raised number names an institution and not an entry."""
    return node.heading == "Front matter" or bool(node.ancestry and node.ancestry[0] == "Front matter")


def _expand_numeric(marker: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"[,;]", marker.replace("^", "")):  # a caret between two numbers of one marker: "12,^14"
        part = part.strip()
        m = re.fullmatch(rf"(\d{{1,3}})(?:\s*{_DASH}\s*|\s+e\s+)(\d{{1,3}})", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < a <= b <= a + 50:
                out.extend(range(a, b + 1))
        elif part.isdigit() and not part.startswith("0"):
            out.append(int(part))  # no entry is numbered "05": "p < .05" is a p-value
    return out


def _bracket_runs(text: str) -> list[tuple[str, list[int]]]:
    """The bracketed markers of a node, a run of them read as one marker.

    Some publishers bracket every entry on its own, so a range comes out as "[7]–[11]" and a
    list as "[12],14,21,[40]" — the entries in between are cited as much as the bracketed ones,
    and reading only the brackets loses them. Two brackets belong to one run when nothing but a
    dash, or commas and bare numbers, stands between them."""
    out: list[tuple[str, list[int]]] = []
    ms = list(_NUMERIC.finditer(text))
    i = 0
    while i < len(ms):
        j = i
        nums = _expand_numeric(ms[i].group(1))
        while j + 1 < len(ms):
            gap = text[ms[j].end() : ms[j + 1].start()]
            nxt = _expand_numeric(ms[j + 1].group(1))
            if not nums or not nxt:
                break
            if re.fullmatch(rf"\s*{_DASH}\s*", gap) and nums[-1] < nxt[0] <= nums[-1] + 50:
                nums.extend(range(nums[-1] + 1, nxt[0]))  # "[7]–[11]"
            elif re.fullmatch(r"\s*,\s*(?:\d{1,3}\s*,\s*)*", gap):
                nums.extend(int(n) for n in re.findall(r"\d{1,3}", gap))  # "[12],14,21,[40]"
            else:
                break
            nums.extend(nxt)
            j += 1
        out.append((text[ms[i].start() : ms[j].end()], nums))
        i = j + 1
    return out


def find_citations(tree: Tree, refs: list[Ref]) -> list[Citation]:
    """Every (node, entry) pair the node's text names, numerically or by author and year."""
    if not refs:
        return []
    numbers = {r.ref_no for r in refs}
    n_refs = max(numbers)
    by_author_year: dict[tuple[str, str], list[int]] = {}
    by_author: dict[str, list[int]] = {}
    for r in refs:
        if r.first_author and r.year:
            key = _norm_name(r.first_author)
            by_author_year.setdefault((key, r.year), []).append(r.ref_no)
            by_author_year.setdefault((key, r.year.rstrip("abcdefg")), []).append(r.ref_no)
            by_author.setdefault(key, []).append(r.ref_no)
    ref_nodes = {r.node_id for r in refs}
    out: list[Citation] = []
    seen: set[tuple[str, int]] = set()
    # the author list and the affiliations carry raised numbers of their own — "Felis^1", "^2Oceans
    # Institute" — and none of them is a citation, so the front matter is not read for markers
    citing = [n for n in tree.walk() if n.type in _CITING_TYPES and n.node_id not in ref_nodes and n.role != "references" and n.text and not _front(n)]
    texts = [n.text.replace("\u00a0", " ") for n in citing]
    # A caret is the layout model saying that the number was printed raised, so where a paper carries
    # carets they are its superscripts and nothing else is: "BaTiO3" and "SiO2" are chemistry, and
    # guessing at them is what turns a formula into a citation. A paper whose superscripts reach the
    # tree without carets falls back to the older guess — the word before three letters or a stop,
    # every number naming an entry, and the style the paper's own.
    bracketed = sum(1 for t in texts for _ in _NUMERIC.finditer(t))
    carets = [x for t in texts for m in _CARET.finditer(t) for x in _expand_numeric(m.group(1))]
    caret_style = len(carets) >= 3 and sum(1 for x in carets if x in numbers) >= 0.8 * len(carets)
    plain = [t.replace("^", "") for t in texts]  # a superscript mark says nothing to the other styles: "[^1]" is "[1]"
    glued = [x for t in plain for m in _glued_markers(t) for x in _expand_numeric(m.group(1))]
    in_range = sum(1 for x in glued if x in numbers)
    superscript_style = bracketed == 0 and len(glued) >= 5 and in_range >= 0.8 * len(glued)
    parens = [x for t in plain for m in _PAREN_NUM.finditer(t) for x in _expand_numeric(m.group(1))]
    paren_style = bracketed == 0 and len(parens) >= 5 and sum(1 for x in parens if x in numbers) >= 0.8 * len(parens)

    def link(node: Node, ref_no: int, marker: str) -> None:
        if ref_no in numbers and (node.node_id, ref_no) not in seen:
            seen.add((node.node_id, ref_no))
            out.append(Citation(node.node_id, ref_no, marker.strip()))

    def author_year(node: Node, surname: str, years: str, marker: str) -> None:
        key = _norm_name(surname.split()[-1] if surname.split()[0].lower() in _PARTICLES else surname.split()[0])
        for year in re.findall(_YEAR, years):
            hits = by_author_year.get((key, year)) or by_author_year.get((key, year.rstrip("abcdefg"))) or []
            for ref_no in sorted(set(hits)):
                link(node, ref_no, marker)

    for node, raw in zip(citing, texts):
        text = raw.replace("^", "")  # a superscript mark says nothing to these styles: "[^1]" is "[1]"
        for marker, nums in _bracket_runs(text):
            for ref_no in nums:
                link(node, ref_no, marker)
        if caret_style:
            for m in _CARET.finditer(raw):
                for ref_no in _expand_numeric(m.group(1)):
                    link(node, ref_no, m.group(1))  # the caret is part of the marker as printed, so the review can find it
        if superscript_style:
            for m in _glued_markers(text):
                for ref_no in _expand_numeric(m.group(1)):
                    link(node, ref_no, m.group(1))
        if paren_style:
            for m in _PAREN_NUM.finditer(text):
                for ref_no in _expand_numeric(m.group(1)):
                    link(node, ref_no, m.group(0))
        if not by_author_year:
            continue
        for group in _PAREN_GROUP.finditer(text):
            for segment in re.split(r";", group.group(1)):
                for m in _IN_PAREN.finditer(segment):
                    author_year(node, m.group(1), m.group(2), m.group(0))
        for m in _NARRATIVE.finditer(text):
            author_year(node, m.group(1), m.group(2), m.group(0))
    return out


def marker_patterns(tree: Tree) -> list[re.Pattern[str]]:
    """The numeric marker styles a paper prints, for the review to look its declined markers up in.
    The bracketed style is always one of them; the caret superscript only where the paper carries
    carets, so that "10^6" in a bracketed paper is not held up as a marker that names no entry."""
    carets = sum(len(re.findall(r"\^\d", n.text)) for n in tree.walk() if n.type in _CITING_TYPES and n.role != "references" and n.text)
    return [_NUMERIC, _CARET] if carets >= 3 else [_NUMERIC]


def link_citations(tree: Tree, jats_xml: bytes | None = None) -> tuple[list[Ref], list[Citation]]:
    refs = reference_entries(tree)
    if jats_xml:
        refs = enrich_from_jats(refs, jats_xml)
    return refs, find_citations(tree, refs)


def summarize(refs: Iterable[Ref], cites: Iterable[Citation]) -> dict[str, int]:
    cites = list(cites)
    return {"refs": sum(1 for _ in refs), "citations": len(cites), "citing_nodes": len({c.node_id for c in cites}), "cited_refs": len({c.ref_no for c in cites})}
