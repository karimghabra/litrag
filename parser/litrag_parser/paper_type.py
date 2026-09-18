"""What kind of paper this is — research, review, case report, letter, editorial, protocol,
data descriptor, correction — and its subtype where a label states one, decided before its
headings are read with that in mind.

Every source of a type speaks a different vocabulary: a JATS file's `article-type` and its
`<subject>` line, Europe PMC's publication types (MeSH's, for MEDLINE papers), the title's
own words, the label a publisher prints above the title. One table (`LABELS`) maps every
spelling seen to one canonical type and subtype, and says whether the label *names* a kind
("Randomized Controlled Trial", "Systematic Review", "Case Report") or is the publisher's
default bucket ("research-article", "Journal Article", "Article"), which names nothing.

The decision: the most trusted *specific* label wins (`TRUST`: the record's, then the file's,
then the subject line's, then the title's, then the printed label's); the subtype is the
first one any agreeing label states; a default alone never makes a research paper — the
paper's shape must agree (a results lane beside a methods or a discussion one; or, with no
results heading, the methods after the discussion as Nature sets them, or measurements
reported in a tenth of the body's paragraphs), else the paper is `other` with the default
named. Every disagreement — two labels, or a label against the
shape — is a note the audit shows, never a silent override. The shape decides on its own
only where its rule was measured precise (`SHAPE_DECIDES`), the printed label likewise
(`PRINTED_DECIDES`), and the profile kind only when switched on.

The shape's rules read one question in order: does the paper report work of its own? A
results heading says yes. No results heading and an abstract says no, and the paper is a
review — a methodology section does not change that, because a review that searches the
literature has one too. Short prose with neither lane is an editorial. Reading the rules
this way, rather than asking each type for its own fingerprint, is what took the `other`
bucket from 41 papers named and 1 right down to 2 named: `other` is a refusal, not an
answer, and the shape now has an answer for all but a handful.

    uv run --project parser python -m litrag_parser.paper_type --fetch --lib …     Europe PMC's record for every paper with a DOI or PMID
    uv run --project parser python -m litrag_parser.paper_type --measure --lib …   every source against the stated labels, the stated ones hidden

The type and subtype are columns (`papers.type`, `subtype`, `type_source`, `type_detail`);
the audit expects what the type promises — no methods in a review is nothing, no methods in
a research article is a warning.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .facets import role_of
from .headings import canonical_of
from .tree import Tree

TYPES = ("research", "review", "case-report", "letter", "editorial", "protocol", "data", "correction", "other")

#: the sources of a type, most trusted first: the record's publication types are an indexer's word,
#: the file's article-type the publisher's, the subject line the publisher's finer word, the title
#: the authors', the printed label the layout model's reading of the page, the shape the reader's own
TRUST = ("record", "jats", "subject", "title", "printed", "shape")

#: what the shape may decide on its own, with no label at all: measured before any kind was let
#: in, and re-measured whenever a rule changes; the rest of the shape's readings are notes
SHAPE_DECIDES = {"research", "review", "case-report", "data", "letter", "editorial"}  # measured on the 248 papers a stated source labels (2026-09-17, the four XML libraries, the stated sources hidden, so the shape and the title are all that is left): review 137 named and 133 right, research 77 and 68, data descriptor 4 of 4, case report 5 of 5, letter 5 of 5, editorial 13 and 11. Editorial is the loosest of them at 0.846 — the two it gets wrong are a letter and a book chapter, and nothing in the shape tells a letter to the editor from an editorial, since they are the same piece of writing. Protocol stays out: the shape names it 4 and gets 3, and the title already names every protocol here, so letting it decide would buy nothing and cost the one it gets wrong. Correction stays out because the shape has no rule for it at all — the title carries that one

#: what a printed label the table does not know may decide through the `type-label` kind: measured
#: on 744 labelled papers, it names research at 0.986 precision and the rest worse
PRINTED_DECIDES = {"research"}


@dataclass(frozen=True)
class Evidence:
    """One source's word: the canonical type, a subtype when the label states one, where it came
    from, the label as written, and whether the label names a kind or is a default bucket."""

    type: str
    subtype: str | None
    source: str
    label: str
    specific: bool


#: the most specific subtype first: an RCT that is also a multicenter and a comparative study is an RCT
_SUBTYPE_RANK = ["rct", "clinical-trial", "systematic-review", "meta-analysis", "scoping-review", "umbrella-review", "narrative-review", "mini-review", "case-series", "multicenter", "observational", "comparative", "evaluation", "brief-report", "methods", "perspective", "comment", "erratum", "retraction", "addendum", "expression-of-concern", "guideline", "abstract", "other"]


def _norm(label: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", label.lower()).split())


_R, _V, _C, _L, _E, _P, _D, _X, _O = "research", "review", "case-report", "letter", "editorial", "protocol", "data", "correction", "other"
LABELS: dict[str, tuple[str, str | None, bool]] = {}


def _add(kind: str, subtype: str | None, specific: bool, *labels: str) -> None:
    for label in labels:
        LABELS[_norm(label)] = (kind, subtype, specific)


# the publisher's default buckets: they name nothing
_add(_R, None, False, "research-article", "journal article", "article", "articles", "research", "research articles", "paper", "papers", "research paper", "original manuscript", "full length article", "full paper", "regular article", "regular paper", "scientific report", "report", "preprint", "other", "misc", "unknown", "sciadv r-articles", "manuscript")
# research, named
_add(_R, None, True, "original research", "original article", "original articles", "original research article", "original paper", "original investigation", "original study", "research report", "empirical study", "full-length original research", "primary research")
_add(_R, "brief-report", True, "brief-report", "brief report", "brief research report", "brief communication", "short communication", "short communications", "short report", "rapid communication", "rapid communications", "communication", "communications", "short paper", "research letter", "research note", "short note")
_add(_R, "rct", True, "randomized controlled trial", "randomised controlled trial", "randomized clinical trial", "randomised clinical trial")
_add(_R, "clinical-trial", True, "clinical trial", "controlled clinical trial", "clinical trial phase i", "clinical trial phase ii", "clinical trial phase iii", "clinical trial phase iv", "clinical trial report", "clinical study", "clinical-trial", "pragmatic clinical trial", "adaptive clinical trial", "equivalence trial", "clinical trial veterinary")
_add(_R, "multicenter", True, "multicenter study", "multicentre study")
_add(_R, "observational", True, "observational study", "cohort study", "cross sectional study", "case control study", "longitudinal study", "retrospective study", "prospective study", "observational study veterinary")
_add(_R, "comparative", True, "comparative study")
_add(_R, "evaluation", True, "evaluation study", "evaluation studies", "validation study", "validation studies")
_add(_R, "methods", True, "methods-article", "methods article", "methods", "technical note", "technical report", "methods and resources", "resource", "resources", "tools and resources", "tools for protein science", "technical advance", "technical advances", "new methods", "methods paper", "software", "software article", "application note", "applications note", "protocol paper", "methodology")
# reviews
_add(_V, None, True, "review-article", "review", "reviews", "review article", "review articles", "review paper", "comprehensive review", "critical review", "topical review", "major review", "literature review", "state of the art review", "invited review", "expert review", "focused review", "concise review", "current opinion", "tutorial review", "review series", "annual review")
_add(_V, "systematic-review", True, "systematic-review", "systematic review", "systematic reviews", "systematic review and meta analysis", "systematic literature review")
_add(_V, "meta-analysis", True, "meta-analysis", "meta analysis", "network meta-analysis")
_add(_V, "scoping-review", True, "scoping review")
_add(_V, "umbrella-review", True, "umbrella review")
_add(_V, "narrative-review", True, "narrative review")
_add(_V, "mini-review", True, "mini-review", "mini review", "minireview", "mini-reviews")
# case reports
_add(_C, None, True, "case-report", "case report", "case reports", "clinical case", "clinical case report", "case presentation", "case-study", "case study", "case studies")
_add(_C, "case-series", True, "case series", "case-series")
# letters
_add(_L, None, True, "letter", "letters", "letter to the editor", "letters to the editor", "letter to editor", "correspondence", "reply", "response", "author reply", "authors reply", "author's reply", "authors' reply")
_add(_L, "comment", True, "comment", "comments", "comment and reply")
# editorials, opinion, commentary
_add(_E, None, True, "editorial", "editorials", "guest editorial", "editorial comment", "editorial material", "introduction", "foreword", "preface")
_add(_E, "perspective", True, "perspective", "perspectives", "opinion", "opinions", "viewpoint", "viewpoints", "commentary", "commentaries", "article-commentary", "article commentary", "debate", "hypothesis and theory", "hypothesis", "opinion article", "opinion piece", "point of view", "position paper", "essay", "discussion", "in brief", "news and views", "research highlight", "interview", "book review", "product review", "news", "meeting report", "conference report", "policy forum", "policy brief")
# protocols
_add(_P, None, True, "protocol", "protocols", "study protocol", "study-protocol", "clinical trial protocol", "trial protocol", "research protocol", "systematic review protocol", "registered report protocol")
# data descriptors
_add(_D, None, True, "data-paper", "data paper", "data descriptor", "data note", "data article", "dataset", "data in brief", "data report", "database", "data-descriptor", "database article")
# corrections
_add(_X, "erratum", True, "correction", "corrections", "erratum", "errata", "corrigendum", "corrigenda", "published erratum", "correction notice", "author correction", "publisher correction")
_add(_X, "retraction", True, "retraction", "retractions", "retraction of publication", "retraction note", "retraction notice", "partial retraction", "retracted article")
_add(_X, "addendum", True, "addendum", "addenda")
_add(_X, "expression-of-concern", True, "expression-of-concern", "expression of concern", "editorial expression of concern")
# other, named
_add(_O, "guideline", True, "practice guideline", "guideline", "guidelines", "consensus statement", "consensus development conference", "position statement", "recommendations", "clinical practice guideline", "consensus")
_add(_O, "abstract", True, "abstract", "abstracts", "meeting abstract", "meeting abstracts", "conference abstract", "poster")
_add(_O, "other", True, "historical article", "biography", "obituary", "lecture", "address", "portrait", "chapter-article", "chapter article", "book chapter", "congress", "conference paper", "proceedings", "proceedings paper", "video-audio media", "webcast", "legal case", "personal narrative", "patient education handout", "newspaper article", "autobiography", "festschrift", "bibliography", "dictionary", "directory", "legislation")


def canonical_label(label: str, source: str) -> Evidence | None:
    """The table's word for one label from one source; None for a label the table does not know
    (a field name — "Chemistry", "academicsubjects/med00200" — or a funding tag)."""
    got = LABELS.get(_norm(label))
    if got is None:
        return None
    kind, subtype, specific = got
    return Evidence(kind, subtype, source, label.strip(), specific)


_ARTICLE_TYPE = re.compile(r"<article\b[^>]*\barticle-type\s*=\s*[\"']([^\"']+)[\"']", re.I)
_CATEGORIES = re.compile(r"<article-categories>(.*?)</article-categories>", re.S | re.I)
_HEADING_GROUP = re.compile(r"<subj-group\b[^>]*subj-group-type=[\"']heading[\"'][^>]*>(.*?)</subj-group>", re.S | re.I)
_SUBJECT = re.compile(r"<subject>(.*?)</subject>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def from_jats(xml: bytes | None) -> list[Evidence]:
    """The root element's `article-type` (source `jats`) and the subject line of the article's
    categories (source `subject`: the heading group's first subject, else every subject), each
    mapped through the table; what the table does not know is left out."""
    if not xml:
        return []
    head = xml[:60000].decode("utf-8", "replace")
    out: list[Evidence] = []
    m = _ARTICLE_TYPE.search(head)
    if m:
        e = canonical_label(m.group(1), "jats")
        if e is not None:
            out.append(e)
    cats = _CATEGORIES.search(head)
    if cats:
        groups = _HEADING_GROUP.findall(cats.group(1))
        subjects = _SUBJECT.findall(groups[0]) if groups else _SUBJECT.findall(cats.group(1))
        seen = 0
        for raw in subjects:
            text = " ".join(_TAG.sub(" ", raw).split())
            e = canonical_label(text, "subject")
            if e is not None:
                out.append(e)
                seen += 1
            if seen >= 2:
                break
    return out


def from_record(pub_types: list[str] | str | None) -> list[Evidence]:
    """Europe PMC's publication types — MeSH's for a MEDLINE paper — each through the table."""
    if not pub_types:
        return []
    raw = [p.strip() for p in (pub_types.split(";") if isinstance(pub_types, str) else pub_types) if p and p.strip()]
    out: list[Evidence] = []
    for r in raw:
        e = canonical_label(r, "record")
        if e is not None:
            out.append(e)
    return out


_TITLE_RULES: list[tuple[re.Pattern[str], str, str | None]] = [
    (re.compile(r"(?:^|[:\-–—]\s*)(?:a |an |the )?(?:study |trial )?protocol (?:for|of)\b|\b(?:study|trial) protocol\b|\bprotocol for an? (?:randomi[sz]ed|systematic|scoping|multicent|prospective|cluster|pilot|feasibility|phase)", re.I), _P, None),
    (re.compile(r"\bsystematic (?:literature )?review\b", re.I), _V, "systematic-review"),
    (re.compile(r"\bmeta-?analys[ie]s\b", re.I), _V, "meta-analysis"),
    (re.compile(r"\bscoping review\b", re.I), _V, "scoping-review"),
    (re.compile(r"\bumbrella review\b", re.I), _V, "umbrella-review"),
    (re.compile(r"\bnarrative review\b", re.I), _V, "narrative-review"),
    (re.compile(r"\bmini-?review\b", re.I), _V, "mini-review"),
    (re.compile(r"(?:^|[:\-–—]\s*)(?:a |an )?(?:(?:comprehensive|critical|brief|short|literature|current|updated|concise|integrative|rapid|state-of-the-art|clinical|historical) )?review(?:\s+(?:of|on|and)\b|\s*$|\s*[:\-–—,(])", re.I), _V, None),
    (re.compile(r"\bcase (?:report|series|presentation)s?\b|^(?:a |an )?(?:rare |unusual |unique )?case of\b", re.I), _C, None),
    (re.compile(r"^(?:editorial )?expression of concern\b(?=\s*[:\-\u2013\u2014]|\s+(?:about|regarding|concerning|for)\b)", re.I), _X, "expression-of-concern"),
    (re.compile(r"^(?:erratum|correction|corrigendum|retraction|addendum)\b(?=\s*[:\-\u2013\u2014]|\s+(?:to|for)\b)|^retracted:", re.I), _X, None),
    (re.compile(r"^(?:reply|response) to\b(?=.*(?:\bet al\b|\(\d{4}\)|\bcomment\b|\bletter\b|\breply\b|\bresponse\b|\bre:))|^letter to the editor\b|^letter:|^in reply\b|^authors?['’]? reply\b|^comment on\b|^re:\s", re.I), _L, None),
    (re.compile(r"^(?:guest )?editorial\b|^perspective:|^commentary:|^viewpoint:|^opinion:", re.I), _E, None),
    (re.compile(r"\brandomi[sz]ed,? (?:(?:controlled|clinical|double[- ]blind|single[- ]blind|placebo[- ]controlled|multicent(?:er|re)|pragmatic|open[- ]label|phase \w+|pilot|feasibility) )*trial\b", re.I), _R, "rct"),
    (re.compile(r"\bdata descriptor\b|^dataset:|\ba dataset of\b", re.I), _D, None),
]


def from_title(title: str | None) -> Evidence | None:
    """What the title says of itself: "… a systematic review and meta-analysis", "Case report: …",
    "Protocol for a randomised …", "Erratum: …", "Reply to …". The first rule that fires."""
    t = (title or "").strip()
    if not t:
        return None
    for pat, kind, subtype in _TITLE_RULES:
        m = pat.search(t)
        if m:
            return Evidence(kind, subtype, "title", m.group(0).strip(" :-–—,("), True)
    return None


def printed_labels(tree: Tree) -> list[str]:
    """The notices the front matter carries: what the publisher printed above the title, one per line."""
    out = []
    for n in tree.walk():
        if n.type == "meta" and n.label == "notice" and n.text:
            out += [line.strip() for line in n.text.split("\n") if line.strip() and len(line.split()) <= 6]
    return out


def from_page(tree: Tree, oracle: Any = None, measured: bool = False) -> list[Evidence]:
    """The labels printed above the title: the table's word where it knows the label; else, with
    an oracle, the `type-label` kind's word where the measurement let it decide (or `measured`)."""
    out: list[Evidence] = []
    for label in printed_labels(tree):
        if role_of(label, meaning=False) != "other" or canonical_of(label)[0] is not None:
            continue  # "INTRODUCTION", "Abstract", "Methods": a heading the layout model set above the title, not the article's type
        e = canonical_label(label, "printed")
        if e is not None:
            out.append(e)
            continue
        if oracle is not None:
            v = oracle.nearest("type-label", label)
            if v.sure and v.name in TYPES and (measured or v.name in PRINTED_DECIDES):
                out.append(Evidence(v.name, None, "printed", label, True))
    return out


_CASE_HEADING = re.compile(r"\bcase (?:presentation|report|description|history|summary|study)\b|\bclinical case\b|\bpatient presentation\b", re.I)
_LETTER_OPENING = re.compile(r"^\s*(?:dear (?:editor|sir|madam|editors|colleagues)|to the editor|sir,|madam,)", re.I)
_REVIEW_ABSTRACT = {"purpose of review", "recent findings", "areas covered", "expert opinion", "summary"}
_REVIEW_METHODS = re.compile(r"\b(?:search strateg|literature search|study selection|data extraction|risk of bias|quality assessment|information sources|prisma|data synthesis|study characteristics|included studies)\b", re.I)  # a systematic review's own headings; "eligibility criteria" and "screening" are a trial's too
_DATA_HEADINGS = re.compile(r"\b(?:data records?|technical validation|usage notes|data description|data descriptor|value of the data|specifications table)\b", re.I)  # Scientific Data's and Data in Brief's fixed headings
_FUTURE = re.compile(r"\bwill (?:be|receive|undergo|include|have|use|take|consist|complete|provide|collect|assess|measure|compare|analy[sz]e|recruit|enrol)\b", re.I)
_STATS = re.compile(r"±|\bp\s*[<=>]\s*0?\.\d|\bn\s*=\s*\d|\bSD\b|\bSEM\b|\bCI\b|\bmean\b|\bmedian\b", re.I)  # a paragraph that reports a measurement
_BODY_LANES = ("other", "methods", "discussion", "results", "results-discussion")
#: an editorial, a commentary, a perspective: opinion prose, no methods, no results, and short.
#: Three thousand words is where the corpus separates them from a review that happens to have
#: no results heading — the shortest such review is 4,586 words, the longest editorial 2,934.
#: The band from 2,500 to 4,000 all score within a point and a half of each other, so the exact
#: number carries little; what carries is that an editorial is short and a review is not.
_EDITORIAL_WORDS = 3000


def shape_of(tree: Tree) -> tuple[dict[str, Any], str | None]:
    """What the tree's own shape says: the lanes it has, a case heading, a letter's opening, a
    structured abstract's labels, its size — and the type those add up to, or None. The rules
    are the reader's; which of them may decide is `SHAPE_DECIDES`, measured."""
    tops = [n for n in tree.root.children if n.type == "section"]
    roles = {n.role for n in tops}
    has_methods = tree.has_methods or "methods" in roles
    has_results = bool(roles & {"results", "results-discussion"})
    has_discussion = bool(roles & {"discussion", "results-discussion"})
    has_abstract = "abstract" in roles
    topical = [n for n in tops if n.role == "other" and n.heading != "Front matter" and n.label != "built" and not (n.heading or "").endswith(("(untitled section)", "(heading not detected)"))]
    paragraphs = [n for n in tree.walk() if n.type == "paragraph"]
    words = sum(len(n.text.split()) for n in paragraphs)
    headings = " | ".join((n.heading or "") for n in tree.walk() if n.type == "section" and n.heading not in ("Front matter",))
    top_headings = " | ".join((n.heading or "") for n in tops if n.heading != "Front matter")
    case_heading = bool(_CASE_HEADING.search(top_headings))  # a case report's own section, not "3.1 Case study" inside a results section
    # the first line of prose after the title, wherever the reader filed it: a letter's "Dear Editor"
    # is a short paragraph the front matter takes as `other` when no heading follows
    first = next((n.text for n in tree.walk() if n.type == "paragraph" and n.role not in ("references", "back")), "") or next((n.text for n in tree.walk() if n.type == "meta" and n.label in ("other", "abstract")), "")
    letter_opening = bool(_LETTER_OPENING.match(first))
    labels = []
    for n in paragraphs:
        if n.role == "abstract":
            m = re.match(r"\s*([A-Za-z][A-Za-z /&]{2,40}?):", n.text)
            if m:
                labels.append(m.group(1).strip().lower())
    review_abstract = any(l in _REVIEW_ABSTRACT for l in labels)
    review_methods = len({m.group(0).lower() for m in _REVIEW_METHODS.finditer(headings)}) >= 2  # a systematic review's methods: a search and a selection, an extraction and a risk of bias — two of them, not one word a trial shares
    data_headings = len({m.group(0).lower() for m in _DATA_HEADINGS.finditer(headings)}) >= 2  # Scientific Data's fixed headings, two of them
    body = " ".join(n.text for n in paragraphs if n.role in ("methods", "results", "results-discussion", "introduction"))
    future = len(_FUTURE.findall(body))  # a protocol is written in the future tense: "participants will be randomised"
    # a paper without a results heading: Nature's order sets the methods after the discussion and the
    # results under the main text's own headings; a research paper's body reports measurements in a
    # tenth or more of its paragraphs (measured: 0.12 to 0.40 on such papers, at most 0.07 on a review
    # with a methodology section, NOTES.md 2026-09-14), where a review's body cites
    body_tops = [n for n in tops if n.role not in ("back", "references", "abstract") and n.heading != "Front matter"]
    last = {n.role: i for i, n in enumerate(body_tops)}
    methods_last = "methods" in last and "discussion" in last and last["methods"] > last["discussion"]
    body_paragraphs = [n for n in paragraphs if n.role in _BODY_LANES and len(n.text.split()) >= 25]
    stats = round(sum(1 for n in body_paragraphs if _STATS.search(n.text)) / len(body_paragraphs), 2) if body_paragraphs else 0.0
    features = {
        "methods": has_methods, "results": has_results, "discussion": has_discussion, "abstract": has_abstract, "topical": len(topical), "sections": len(tops),
        "paragraphs": len(paragraphs), "words": words, "case_heading": case_heading, "letter_opening": letter_opening, "abstract_labels": labels[:6],
        "review_methods": review_methods, "data_headings": data_headings, "future": future, "methods_last": methods_last, "stats": stats,
    }
    verdict: str | None = None
    if case_heading:
        verdict = _C
    elif letter_opening:
        verdict = _L
    elif data_headings:
        verdict = _D
    elif has_methods and future >= 12 and not has_results:
        verdict = _P
    elif review_methods:
        verdict = _V
    elif (has_results and (has_methods or has_discussion)) or (has_methods and has_discussion and (methods_last or stats >= 0.10)):
        verdict = _R  # a results heading beside a methods or a discussion one — the paper reports work of its own, whether or not the methods got a heading of their own; or, with no results heading, Nature's order or a body that reports measurements
    elif not has_methods and not has_results and words < _EDITORIAL_WORDS and len(topical) < 3 and not review_abstract:
        verdict = _E  # short opinion prose with neither lane: an editorial, a commentary, a perspective — and a letter that does not open "Dear Editor", which nothing in the shape tells apart from them. A mini-review is short too, so a paper that has divided itself into topics, or labels its abstract "Purpose of review", is left to the review rule below
    elif not has_results and has_abstract:
        verdict = _V  # an abstract and no results: the paper reports no experiment of its own. A methodology section does not make it research — a review that searches the literature has one too — and the research rule above has already taken every paper whose body measures
    return features, verdict


def _summary(features: dict[str, Any]) -> str:
    lanes = [k for k in ("methods", "results", "discussion", "abstract") if features.get(k)]
    return f"lanes {', '.join(lanes) or 'none'}; {features['topical']} topical sections of {features['sections']}; {features['words']} words" + (f"; statistics in {round(features['stats'] * 100)} % of the body" if features.get("stats") else "") + ("; the methods last" if features.get("methods_last") else "") + ("; a case heading" if features.get("case_heading") else "") + ("; opens to the editor" if features.get("letter_opening") else "") + ("; a review's methods" if features.get("review_methods") else "") + ("; a data descriptor's headings" if features.get("data_headings") else "") + (f"; {features['future']} future-tense verbs" if features.get("future", 0) >= 12 else "")


def profile_of(tree: Tree) -> str:
    """What the paper's shape says as text: its title, the first sentences of its abstract, and
    its top-level headings in order — for the `profile` kind, which is off by default."""
    abstract = ""
    for n in tree.root.children:
        if n.type == "section" and n.role == "abstract":
            text = " ".join(c.text for c in n.children if c.type == "paragraph")
            abstract = " ".join(re.split(r"(?<=[.!?])\s+", text)[:3])[:400]
            break
    heads = [n.heading for n in tree.root.children if n.type == "section" and n.heading and n.heading not in ("Front matter",)]
    return f"Title: {tree.title}. Abstract: {abstract or '(none)'} Sections: {'; '.join(heads[:14]) or '(no headings)'}"


def profile_on() -> bool:
    """The `profile` kind decides only with `LITRAG_TYPE_PROFILE=on`: measured at 0.66 accuracy
    (research 0.904 precision, review 0.674, protocol 0.023 — it names protocol for a research
    paper 39 times in 43), it is a weak signal, shipped off."""
    return os.environ.get("LITRAG_TYPE_PROFILE", "").strip().lower() in ("on", "1", "true", "yes")


def evidence_of(tree: Tree, *, jats_xml: bytes | None = None, pub_types: list[str] | str | None = None, oracle: Any = None, measured: bool = False, hide: set[str] | frozenset[str] = frozenset()) -> list[Evidence]:
    """Every source's word, `hide` left out, in the order the sources are trusted."""
    ev = [*from_record(pub_types), *from_jats(jats_xml)]
    t = from_title(tree.title)
    if t is not None:
        ev.append(t)
    ev += from_page(tree, oracle, measured)
    ev = [e for e in ev if e.source not in hide]
    return sorted(ev, key=lambda e: TRUST.index(e.source) if e.source in TRUST else len(TRUST))


def decide(tree: Tree, *, jats_xml: bytes | None = None, pub_types: list[str] | str | None = None, oracle: Any = None, measured: bool = False, hide: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    """`{type, subtype, source, detail, notes}`: the most trusted specific label; else the shape
    where it may decide (a default label confirmed by the shape is `default`); else `other`.
    `measured` lets every reading decide, for `--measure`; `hide` drops sources."""
    ev = evidence_of(tree, jats_xml=jats_xml, pub_types=pub_types, oracle=oracle, measured=measured, hide=hide)
    features, shape = shape_of(tree)
    notes: list[dict[str, Any]] = []
    root = tree.root.node_id
    specific = [e for e in ev if e.specific]
    defaults = [e for e in ev if not e.specific]
    if specific:
        top = specific[0]
        kind = top.type
        subs = [e.subtype for e in specific if e.type == kind and e.subtype]
        subtype = min(subs, key=lambda s: _SUBTYPE_RANK.index(s) if s in _SUBTYPE_RANK else len(_SUBTYPE_RANK)) if subs else None
        dissent = [e for e in specific if e.type != kind]
        if dissent:
            notes.append({"kind": "type-disagreement", "node_id": root, "page": None, "message": f"the {top.source} says {kind} ({top.label}); " + "; ".join(f"the {e.source} says {e.type} ({e.label})" for e in dissent[:3]) + f" — the {top.source}'s word stands"})
        if shape is not None and shape != kind and not (shape == _R and kind in (_P, _D)):
            notes.append({"kind": "type-disagreement", "node_id": root, "page": None, "message": f"the {top.source} says {kind} ({top.label}); the shape reads as {shape} ({_summary(features)}) — the {top.source}'s word stands"})
        detail = "; ".join(f"{e.source}: {e.label}" for e in specific[:4])
        return {"type": kind, "subtype": subtype, "source": top.source, "detail": detail, "notes": notes}
    if shape is not None and (measured or shape in SHAPE_DECIDES):
        if defaults:
            if shape == _R:
                return {"type": _R, "subtype": None, "source": "default", "detail": f"{defaults[0].source}: {defaults[0].label}, a default bucket; the shape agrees ({_summary(features)})", "notes": notes}
            notes.append({"kind": "type-disagreement", "node_id": root, "page": None, "message": f"the {defaults[0].source} says {defaults[0].label}, a default bucket; the shape reads as {shape} ({_summary(features)})"})
        return {"type": shape, "subtype": None, "source": "shape", "detail": _summary(features), "notes": notes}
    if oracle is not None and (measured or profile_on()):
        v = oracle.nearest("profile", profile_of(tree))
        if v.sure and v.name in TYPES:
            return {"type": v.name, "subtype": None, "source": "meaning", "detail": f"cosine {v.score} margin {v.margin}", "notes": notes}
    if defaults:
        d = defaults[0]
        return {"type": _O, "subtype": None, "source": "default", "detail": f"{d.source}: {d.label}, a default bucket the shape does not confirm ({_summary(features)})", "notes": notes}
    return {"type": _O, "subtype": None, "source": "none", "detail": _summary(features), "notes": notes}


# -- the measurement and the fetch --------------------------------------------------------------------

STATED = ("record", "jats", "subject")


def measure(libs: list[Path], oracle: Any) -> dict[str, Any]:
    """On every paper a stated source labels specifically (the record, the file, its subject
    line), the truth is the most trusted of those; then each other source is scored alone — the
    title, the printed label, the shape, the subject line against the file — and the whole
    decision with the stated sources hidden, which is what a PDF without a record gets."""
    from .library import parsed_papers
    from .recover import recover_from_pdf
    from .tree import build_tree

    truth_n = 0
    by_truth: Counter[str] = Counter()
    labelled_by: Counter[str] = Counter()
    per: dict[str, dict[str, Counter]] = {}  # source → {"answered": Counter[truth], "named": Counter[guess], "correct": Counter[type], "confusion": Counter[(truth, guess)]}
    agree: Counter[str] = Counter()
    subtypes: Counter[str] = Counter()

    def score(source: str, truth: str, guess: str | None) -> None:
        p = per.setdefault(source, {"answered": Counter(), "named": Counter(), "correct": Counter(), "confusion": Counter()})
        if guess is None:
            return
        p["answered"][truth] += 1
        p["named"][guess] += 1
        if guess == truth:
            p["correct"][truth] += 1
        else:
            p["confusion"][(truth, guess)] += 1

    for lib in libs:
        for row in parsed_papers(lib):
            xml = row["source"].read_bytes() if row["format"] == "jats" and row["source"] and row["source"].exists() else None
            doc = json.loads(row["raw"].read_text("utf-8"))
            recover_from_pdf(doc, row["source"] if row["format"] == "pdf" else None)
            tree = build_tree(doc, row["key"])
            ev = evidence_of(tree, jats_xml=xml, pub_types=row.get("pub_types"), oracle=oracle, measured=True)
            stated = [e for e in ev if e.specific and e.source in STATED]
            if not stated:
                continue
            truth = stated[0]
            truth_n += 1
            by_truth[truth.type] += 1
            labelled_by[truth.source] += 1
            if truth.subtype:
                subtypes[truth.subtype] += 1
            for e in stated[1:]:
                agree["agree" if e.type == truth.type else "disagree"] += 1
            # each source alone
            for src in ("title", "printed", "subject"):
                if src == truth.source:
                    continue
                mine = [e for e in ev if e.source == src and e.specific]
                score(src, truth.type, mine[0].type if mine else None)
            features, shape = shape_of(tree)
            score("shape", truth.type, shape)
            # the whole decision with the stated sources hidden: a PDF without a record
            got = decide(tree, jats_xml=xml, pub_types=row.get("pub_types"), oracle=oracle, measured=True, hide=set(STATED))
            score("cascade", truth.type, got["type"] if got["source"] != "none" else None)
            got_shipped = decide(tree, jats_xml=xml, pub_types=row.get("pub_types"), oracle=oracle, hide=set(STATED))
            score("cascade-shipped", truth.type, got_shipped["type"])
    out: dict[str, Any] = {"labelled": truth_n, "labelled_by": dict(labelled_by), "by_truth": dict(by_truth), "subtypes": dict(subtypes.most_common()), "stated_sources": dict(agree), "by_source": {}}
    for src, p in per.items():
        n = sum(p["answered"].values())
        right = sum(p["correct"].values())
        per_type = {}
        for t in TYPES:
            named, correct, have = p["named"][t], p["correct"][t], p["answered"][t]
            if named or have:
                per_type[t] = {"truth": have, "named": named, "correct": correct, "precision": round(correct / named, 3) if named else None, "recall": round(correct / have, 3) if have else None}
        out["by_source"][src] = {"answered": n, "right": right, "accuracy": round(right / n, 3) if n else None, "per_type": per_type, "confusion": {f"{t}→{g}": v for (t, g), v in sorted(p["confusion"].items())}}
    return out



def fetch(libs: list[Path], sleep: float = 0.2) -> dict[str, int]:
    """Europe PMC's record — publication types, authors, journal, year — for every paper with a
    DOI or PMID and any of them missing; what is stored already is kept."""
    from .record import lookup_record
    from .store import open_store, set_record

    done: Counter[str] = Counter()
    for lib in libs:
        conn = open_store(Path(lib) / "store.sqlite")
        rows = conn.execute("SELECT key, doi, pmid FROM papers WHERE (pub_types IS NULL OR authors IS NULL OR journal IS NULL) AND (doi IS NOT NULL OR pmid IS NOT NULL)").fetchall()
        for r in rows:
            rec = lookup_record(r["doi"], r["pmid"])
            if rec:
                set_record(conn, r["key"], **rec)
                done["fetched"] += 1
            else:
                done["unknown"] += 1
            time.sleep(sleep)
        conn.close()
    return dict(done)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="litrag_parser.paper_type", description=__doc__.split("\n\n")[0])
    ap.add_argument("--lib", action="append", default=[], help="a library directory (repeatable)")
    ap.add_argument("--fetch", action="store_true", help="store Europe PMC's record (publication types, authors, journal, year) for every paper with a DOI or PMID")
    ap.add_argument("--measure", action="store_true", help="every source of the type against the stated labels, the stated ones hidden")
    ap.add_argument("--json", help="save the measurement here")
    args = ap.parse_args(argv)
    libs = [Path(l).expanduser() for l in args.lib]
    if not libs or not (args.fetch or args.measure):
        ap.print_help()
        return 2
    if args.fetch:
        print("Europe PMC:", fetch(libs))
    if args.measure:
        from . import lanes

        lanes.configure_from_env(libs[0].resolve().parent)
        r = measure(libs, lanes.active())
        print(f"{r['labelled']} papers labelled by a stated source {r['labelled_by']} · truth by type {r['by_truth']} · subtypes {r['subtypes']} · a second stated source {r['stated_sources']}")
        for src, m in r["by_source"].items():
            print(f"  {src:<16} answered {m['answered']:>4} · right {m['right']:>4} · accuracy {m['accuracy']}")
            for t, v in m["per_type"].items():
                print(f"           {t:<12} truth {v['truth']:>4} · named {v['named']:>4} · correct {v['correct']:>4} · precision {v['precision']} · recall {v['recall']}")
            if m["confusion"]:
                print("           confusions:", m["confusion"])
        if args.json:
            Path(args.json).write_text(json.dumps(r, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
