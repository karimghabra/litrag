"""In-text citations, linked to the reference list.

A paper's reference list sits at its end as one node per entry; its sentences cite those
entries inline — "[6,9,12]" in a numbered journal, "(Lyon, 2020; Fields and Levin 2022)"
or "Levin (2019)" in an author–year one. Both are found in each node's text and tied to
the entry they name, so a node knows which papers it leans on and an entry knows which
nodes lean on it: rows in `refs` and `citations`. A JATS file names its entries outright
(`<ref id>` with DOI and PMID), so those enrich the rows; a PDF gives only the entry's
text, from which a first author, a year and a DOI are read.

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
_NUMERIC = re.compile(r"\[(\d{1,3}(?:\s*[–-]\s*\d{1,3})?(?:\s*,\s*\d{1,3}(?:\s*[–-]\s*\d{1,3})?)*)\]")
# a superscript citation the layout model glued to the word before it: "injury.1 Worldwide", "tendons2,3 and"
_GLUED = re.compile(r"(?:[A-Za-z]{3,}|(?<!\d)[.,;:)\]])\^?(\d{1,3}(?:[,–-]\d{1,3})*)(?=[\s.,;:)\]]|$)")  # not "86.4", not "3.1 mg"; "al.^29" as well as "al.29"
# the same superscript with the layout model's space before it, after a stop and before a capital: "applications. 17 While", "medicine. 29,30 The"
_SPACED = re.compile(r"(?<=[.,;:)\]]) (\d{1,3}(?:[,–-]\d{1,3})*)(?= [A-Z(]|$)")
# numbers in parentheses — "(1)", "(1, 2)", "(3–5)" — the style of Frontiers, Science and NAR, read only when the paper brackets nothing and most of them name an entry
_PAREN_NUM = re.compile(r"(?<![A-Za-z\d.])\((\d{1,3}(?:\s*[–-]\s*\d{1,3})?(?:\s*,\s*\d{1,3}(?:\s*[–-]\s*\d{1,3})?)*)\)(?![A-Za-z\d])")
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


def reference_entries(tree: Tree) -> list[Ref]:
    """One Ref per entry in the references lane, numbered in order."""
    entries = [n for n in tree.walk() if n.role == "references" and n.type in ("list_item", "paragraph") and n.text.strip()]
    refs: list[Ref] = []
    printed: list[int | None] = []
    for i, n in enumerate(entries):
        text = re.sub(r"\s+", " ", n.text).strip()
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


def _expand_numeric(marker: str) -> list[int]:
    out: list[int] = []
    for part in marker.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d{1,3})\s*[–-]\s*(\d{1,3})", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < a <= b <= a + 50:
                out.extend(range(a, b + 1))
        elif part.isdigit():
            out.append(int(part))
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
    citing = [n for n in tree.walk() if n.type in _CITING_TYPES and n.node_id not in ref_nodes and n.role != "references" and n.text]
    # Superscript numbers are read only when the paper brackets nothing and glues plenty:
    # "µm3" or "CD34" must never become a citation, so the word before must be three letters
    # or a punctuation mark, every number must name an entry, and the style must be the paper's.
    bracketed = sum(1 for n in citing for _ in _NUMERIC.finditer(n.text))
    glued = [x for n in citing for m in list(_GLUED.finditer(n.text.replace(" ", " "))) + list(_SPACED.finditer(n.text.replace(" ", " "))) for x in _expand_numeric(m.group(1))]
    in_range = sum(1 for x in glued if x in numbers)
    superscript_style = bracketed == 0 and len(glued) >= 5 and in_range >= 0.8 * len(glued)
    parens = [x for n in citing for m in _PAREN_NUM.finditer(n.text) for x in _expand_numeric(m.group(1))]
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

    for node in citing:
        text = node.text.replace(" ", " ").replace("^", "")  # a superscript mark says nothing here: "[^1]" is "[1]"
        for m in _NUMERIC.finditer(text):
            for ref_no in _expand_numeric(m.group(1)):
                link(node, ref_no, m.group(0))
        if superscript_style:
            for m in list(_GLUED.finditer(text)) + list(_SPACED.finditer(text)):
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


def link_citations(tree: Tree, jats_xml: bytes | None = None) -> tuple[list[Ref], list[Citation]]:
    refs = reference_entries(tree)
    if jats_xml:
        refs = enrich_from_jats(refs, jats_xml)
    return refs, find_citations(tree, refs)


def summarize(refs: Iterable[Ref], cites: Iterable[Citation]) -> dict[str, int]:
    cites = list(cites)
    return {"refs": sum(1 for _ in refs), "citations": len(cites), "citing_nodes": len({c.node_id for c in cites}), "cited_refs": len({c.ref_no for c in cites})}
