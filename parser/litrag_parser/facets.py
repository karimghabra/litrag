"""Which lane a heading names.

The role of a node is the role of the nearest top-level section header above
it, inherited down the tree. A heading is normalised — numbering, Roman
numerals, trailing colons stripped — and matched against a finite vocabulary.
Anything that does not match cleanly is `other`: a wrong facet is invisible at
query time, a missing one is merely a gap.
"""

from __future__ import annotations

import re

ROLES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^abstract$|^summary$"), "abstract"),
    (re.compile(r"^(introduction|background)$"), "introduction"),
    (
        re.compile(
            r"^(materials?\s*(and|&)\s*methods?|methods?|experimental|experimental\s+(section|procedures?|methods?|details?)|methodology|materials?,?\s*methods?(\s*and\s*\w+)?|patients?\s+and\s+methods?|subjects?\s+and\s+methods?|surgical\s+technique)$"
        ),
        "methods",
    ),
    (re.compile(r"^(results?\s*(and|&)\s*discussion|results?,?\s*discussion)$"), "results-discussion"),
    (re.compile(r"^(results?|findings|experimental\s+results?)$"), "results"),
    (re.compile(r"^(discussion|conclusions?|concluding\s+remarks|conclusions?\s*(and|&)\s*(outlook|perspectives?|future\s+\w+)|discussion\s*(and|&)\s*conclusions?|summary\s*(and|&)\s*conclusions?|limitations|outlook|perspectives?)$"), "discussion"),
    (re.compile(r"^(references?|bibliography|literature\s+cited|works\s+cited)$"), "references"),
    (
        re.compile(
            r"^(acknowledg\w*|funding(\s+\w+)?|author\s+contributions?|authors?'?\s+contributions?|conflicts?\s+of\s+interest|competing\s+interests?|declaration\s+of\s+\w+|data\s+availability(\s+statement)?|supplementary(\s+\w+)*|supporting\s+information|appendix(\s+\w+)?|abbreviations|ethics\s+\w+|ethical\s+\w+|consent\s+\w+|disclosures?|notes?|orcid|highlights|keywords?|graphical\s+abstract|footnotes?|cited\s+works)$"
        ),
        "back",
    ),
]

_NUMBERING = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)*\.?)|(?:[ivxlcdm]+\.)|(?:[a-z]\.)|(?:\(?[a-z0-9]\)))\s*[.)\-–—:]?\s+",
    re.IGNORECASE,
)


def normalise(heading: str) -> str:
    """"2.1. Materials and Methods:" → "materials and methods"."""
    h = heading.strip()
    h = _NUMBERING.sub("", h)
    h = re.sub(r"[:.\s]+$", "", h)
    h = re.sub(r"\s+", " ", h)
    return h.strip().lower()


def role_of(heading: str) -> str:
    """The lane a heading names, or `other` when it names none cleanly."""
    clean = normalise(heading)
    if not clean:
        return "other"
    for pattern, role in ROLES:
        if pattern.match(clean):
            return role
    return "other"


#: The lanes retrieval partitions on. `references` and `back` are kept as
#: nodes for provenance but are not a facet anyone searches.
FACETS = ("abstract", "introduction", "methods", "results", "results-discussion", "discussion", "other")
