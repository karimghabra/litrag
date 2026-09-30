"""Which lane a heading names.

The role of a node is the role of the nearest top-level section header above
it, inherited down the tree. A heading is normalised — numbering, Roman
numerals, trailing colons stripped — and matched against a finite vocabulary.
Anything that does not match cleanly is asked of the embedder in `lanes.py`, when one is
configured, and is `other` when that has no clear answer either: a wrong facet is
invisible at query time, a missing one is merely a gap.
"""

from __future__ import annotations

import os
import re

from .lanes import by_meaning


def vocabulary_on() -> bool:
    """`LITRAG_VOCABULARY=off` is the experiment: no heading is named by the vocabulary, the
    embedder names every one alone (abstract and references included), and the corpus
    harness says what that costs. Never the default."""
    return os.environ.get("LITRAG_VOCABULARY", "on") != "off"

ROLES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(abstract|summary|lay summary|structured abstract|synopsis)$"), "abstract"),
    (re.compile(r"^(introduction|background|main|main text)$"), "introduction"),  # "Main", "Main text": Nature's and OUP's wrapper for the body, whose own paragraphs are the introduction
    (
        re.compile(
            r"^(materials?\s*(and|&)\s*(experimental\s+)?methods?|methods?\s*(and|&)\s*\w+(\s+\w+)?|methods?|star\W*methods|method\s+details|experimental|experimental\s+(sections?|procedures?|methods?|methodolog(y|ies)|details?|design|setup|part|approach(es)?|work)(\s*/\s*methods?)?|experimentation|(proposed\s+|research\s+|study\s+)?methodolog(y|ies)|materials?,?\s*methods?(\s*and\s*\w+)?|(patients?|subjects?|participants?|animals?|data|design|research\s+design|materials?|study\s+design|population)\s+and\s+methods?|research\s+design\s+and\s+methods?|surgical\s+technique|study\s+design(\s+and\s+\w+)?|materials?)$"
        ),
        "methods",
    ),
    (re.compile(r"^(results?\s*(and|&)\s*discussion|results?,?\s*discussion)$"), "results-discussion"),
    (re.compile(r"^(results?|findings|experimental\s+results?)$"), "results"),
    (re.compile(r"^(discussion|conclusions?|concluding\s+remarks|conclusions?\s*(and|&)\s*(outlook|perspectives?|future\s+\w+)|discussion\s*(and|&)\s*conclusions?|summary\s*(and|&)\s*conclusions?|limitations|outlook|perspectives?)$"), "discussion"),
    (re.compile(r"^(references?|bibliography|literature\s+cited|works\s+cited)$"), "references"),
    (
        re.compile(
            r"^(statement\s+of\s+significance|significance\s+statement|article\s+(info|information|history)|author\s+information|highlights|graphical\s+abstract|acknowledg\w*|funding(\s+\w+)?|author\s+contributions?|authors?'?\s+contributions?|conflicts?\s+of\s+interest|competing\s+interests?|declaration\s+of\s+\w+|data\s+availability(\s+statement)?|supplementary(\s+\w+)*|supporting\s+information|appendix(\s+\w+)?|abbreviations|ethics\s+\w+|ethical\s+\w+|consent\s+\w+|disclosures?|notes?|orcid|highlights|keywords?|graphical\s+abstract|footnotes?|cited\s+works)$"
        ),
        "back",
    ),
]

_NUMBERING = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)*\.?)|(?:[ivxlcdm]+\.)|(?:[a-z]\.)|(?:\(?[a-z0-9]\)))\s*[.)\-–—:]?\s+",
    re.IGNORECASE,
)


def unspace(heading: str) -> str:
    """Elsevier's letter-spaced headings: "a b s t r a c t" → "abstract", "a r t i c l e i n f o" → "article info"."""
    tokens = heading.strip().split()
    if len(tokens) >= 4 and all(len(t) == 1 for t in tokens):
        joined = "".join(tokens)
        for known in ("abstract", "articleinfo", "keywords", "introduction", "highlights", "references"):
            if joined.lower() == known:
                return {"articleinfo": "article info"}.get(known, known)
        return joined
    return heading


def normalise(heading: str) -> str:
    """"2.1. Materials and Methods:" → "materials and methods"."""
    h = unspace(heading.strip())
    h = re.sub(r"\s*\|\s*", " ", h)  # Wiley's "3 | Results"
    h = _NUMBERING.sub("", h)
    h = re.sub(r"[:.\s]+$", "", h)
    h = re.sub(r"\s+", " ", h)
    return h.strip().lower()


def role_of(heading: str, meaning: bool = True) -> str:
    """The lane a heading names, or `other` when it names none cleanly. With `meaning`
    off, the vocabulary alone answers — what decides a heading's depth, since "Statistical
    analysis" under "Methods" is methods by meaning but not a top-level section."""
    clean = normalise(heading)
    if not clean:
        return "other"
    strict = vocabulary_on()
    if strict:
        for pattern, role in ROLES:
            if pattern.match(clean):
                return role
    if not meaning:
        return "other"
    lane = by_meaning(clean, alone=not strict)  # the embedder, when one is configured; `other` otherwise
    if lane != "other":
        return lane
    if not strict:
        return "other"  # the experiment: nothing but the embedder names a heading
    # with no embedder: a short heading that carries the word itself — "Methods and Dataset",
    # "Proposed Methodology" — is methods when nothing in it says results or discussion
    if len(clean.split()) <= 5 and re.search(r"\b(methods?|methodolog(y|ies)|experimental\s+(section|procedures?|setup))\b", clean) and not re.search(r"\b(results?|discussion|supplementar|availability|conclusion)", clean):
        return "methods"
    return "other"


#: The lanes retrieval partitions on. `references` and `back` are kept as
#: nodes for provenance but are not a facet anyone searches.
FACETS = ("abstract", "introduction", "methods", "results", "results-discussion", "discussion", "other")
