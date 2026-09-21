"""The metrics, and the proof that each of them can fail.

A measure nobody has seen break is not evidence. `PLAN.md` §3 asks for each one to be shown
reacting to a mutation of its input before any number measured with it is believed: delete a
paragraph and watch conservation drop, swap two sections' lanes and watch precision drop,
shuffle the labels and watch it fall to chance, plant a paper in two splits and watch the leak
check fire. That is what most of this file is.
"""

import copy
import json
import random
from pathlib import Path

import pytest

from litrag_parser.evaluate import (
    Landing, accounting, bootstrap, by_lane, check_no_leak, confusion, coverage_of, landings,
    precision_and_coverage, precision_of, redact, risk_coverage, SplitViolation,
)
from litrag_parser.tree import build_tree

FIXTURES = Path(__file__).parent / "fixtures"
KEY = "doi:10.3390/mi15070851"


def _trees():
    pdf = build_tree(json.loads((FIXTURES / "PMC11278924.docling.json").read_text("utf-8")), KEY)
    xml = build_tree(json.loads((FIXTURES / "PMC11278924.jats.docling.json").read_text("utf-8")), KEY)
    return pdf, xml


def _rows(pdf=None, xml=None, **kw):
    a, b = _trees()
    return landings(pdf or a, xml or b, paper=KEY, prefix="10.3390", split="DEV",
                    familiar=True, **{"paper_type": "research", **kw})


# ---- the measures say something on a paper that is read well -------------------------------


def test_the_fixture_paper_is_mostly_asserted_and_mostly_right():
    rows = _rows()
    got = precision_and_coverage(rows)
    assert got["paragraphs"] > 30 and got["publishers"] == 1
    assert got["precision"] >= 0.9 and got["coverage"] >= 0.9
    assert got["correct"] + got["wrong"] == got["asserted"]


def test_where_the_unasserted_went_is_reported_not_just_counted():
    """A reader can reach any precision by asserting less, so the report has to say what it did
    instead — prose swallowed by front matter is a loss, not an honest silence."""
    rows = _rows()
    got = precision_and_coverage(rows)
    assert isinstance(got["not_asserted"], dict)
    assert sum(got["not_asserted"].values()) == got["paragraphs"] - got["asserted"]

    lost = [Landing(**{**r.__dict__, "pdf_lane": None, "asserted": False, "correct": False,
                       "where": "front matter"}) for r in rows]
    assert precision_and_coverage(lost)["not_asserted"] == {"front matter": len(rows)}
    assert precision_and_coverage(lost)["precision"] is None  # and it buys no precision at all


def test_a_silence_is_not_an_error():
    """`other` is the reader declining to say. It must cost coverage and not precision."""
    rows = _rows()
    silent = [Landing(**{**r.__dict__, "pdf_lane": "other", "asserted": False, "correct": False,
                         "where": "silent"}) for r in rows]
    got = precision_and_coverage(silent)
    assert got["coverage"] == 0.0
    assert got["precision"] is None  # nothing asserted: there is no precision to report
    assert got["wrong"] == 0


# ---- the mutations: each measure, shown failing --------------------------------------------


class _Line:
    """What `recover.pdf_lines` returns, as much of it as the accounting reads."""

    def __init__(self, text: str):
        self.text = text


@pytest.fixture
def layer_from(monkeypatch):
    """Stand a PDF's text layer up from whatever text is given.

    The repository's fixtures are saved Docling documents, not PDFs, so conservation would
    otherwise be the one measure with no test — and it is the only measure that runs on the
    libraries that have no XML twin."""

    def use(texts):
        import litrag_parser.recover as rec

        monkeypatch.setattr(rec, "pdf_lines", lambda path: {1: [_Line(t) for t in texts]})

    return use


def test_conservation_falls_when_a_paragraph_is_deleted(layer_from):
    pdf, _ = _trees()
    prose = [n.text for n in pdf.walk() if n.type == "paragraph" and len((n.text or "").split()) > 40]
    assert len(prose) > 3
    layer_from(prose)  # a text layer that is exactly what the tree holds

    whole = accounting(pdf, Path("x.pdf"))
    assert whole["accounted"] == 1.0 and whole["unaccounted"] == 0

    cut = copy.deepcopy(pdf)
    victim = next(n for n in cut.walk() if n.type == "paragraph" and (n.text or "") == prose[0])
    victim.text = ""
    after = accounting(cut, Path("x.pdf"))
    assert after["in_a_node"] < whole["in_a_node"]
    assert after["accounted"] < whole["accounted"]
    assert after["unaccounted"] > 0  # and it is named as unaccounted, not quietly absent


def test_text_left_out_on_purpose_is_accounted_for_and_text_left_out_silently_is_not(layer_from):
    """A dropped record is the difference between an omission and a loss. If dropped text did
    not count, a reader would be punished for saying what it left out; if unaccounted text
    counted, `dropped` would become a place to hide losses."""
    pdf, _ = _trees()
    sentence = "the collagen scaffolds were crosslinked with genipin for twenty four hours"
    layer_from([sentence])

    bare = copy.deepcopy(pdf)
    for n in bare.walk():
        n.text, n.heading, n.table = "", None, None
    bare.title = ""
    bare.dropped_items = []

    lost = accounting(bare, Path("x.pdf"))
    assert lost["accounted"] == 0.0 and lost["unaccounted"] == lost["layer_words"] > 0

    bare.dropped_items = [{"kind": "furniture", "page": 1, "text": sentence}]
    said = accounting(bare, Path("x.pdf"))
    assert said["accounted"] == 1.0
    assert said["in_a_node"] == 0 and said["in_a_dropped_record"] == said["layer_words"]


def test_a_word_held_once_and_printed_twice_is_one_word_short(layer_from):
    """Counted as a multiset: a page that says "genipin genipin" and a tree that says it once is
    half a word short, not whole. Set membership would call a truncated paragraph complete."""
    pdf, _ = _trees()
    layer_from(["genipin genipin"])
    bare = copy.deepcopy(pdf)
    for n in bare.walk():
        n.text, n.heading, n.table = "", None, None
    bare.dropped_items = []
    bare.title = "genipin"
    got = accounting(bare, Path("x.pdf"))
    assert got["layer_words"] == 2 and got["in_a_node"] == 1 and got["accounted"] == 0.5


def test_precision_falls_when_two_sections_swap_their_lanes():
    rows = _rows()
    before = precision_of(rows)
    swapped = []
    for r in rows:
        lane = r.pdf_lane
        if lane == "methods":
            lane = "results"
        elif lane == "results":
            lane = "methods"
        swapped.append(Landing(**{**r.__dict__, "pdf_lane": lane,
                                  "correct": bool(r.asserted and lane == r.xml_lane)}))
    after = precision_of(swapped)
    assert after < before, (before, after)


def test_precision_falls_to_chance_when_the_labels_are_shuffled():
    rows = _rows()
    rng = random.Random(3)
    pool = [r.xml_lane for r in rows]
    rng.shuffle(pool)
    shuffled = [Landing(**{**r.__dict__, "pdf_lane": lane, "asserted": lane != "other",
                           "correct": lane != "other" and lane == r.xml_lane})
                for r, lane in zip(rows, pool)]
    assert precision_of(shuffled) < 0.6 < precision_of(rows)


def test_coverage_falls_when_the_reader_abstains_more():
    rows = _rows()
    half = [Landing(**{**r.__dict__, "asserted": r.asserted and i % 2 == 0,
                       "correct": r.correct and i % 2 == 0})
            for i, r in enumerate(rows)]
    assert coverage_of(half) < coverage_of(rows)


def test_the_leak_check_fires_when_a_publisher_is_in_two_splits():
    clean = {"fitted_prefixes": ["10.3390"], "papers": [
        {"prefix": "10.1234", "split": "DEV", "doi": "10.1234/a", "pmcid": "PMC1"},
        {"prefix": "10.5678", "split": "VAL", "doi": "10.5678/b", "pmcid": "PMC2"},
    ]}
    assert check_no_leak(clean) == []

    leaky = copy.deepcopy(clean)
    leaky["papers"].append({"prefix": "10.1234", "split": "SEALED", "doi": "10.1234/c", "pmcid": "PMC3"})
    assert any("10.1234 is in both" in t for t in check_no_leak(leaky))

    twice = copy.deepcopy(clean)
    twice["papers"].append({"prefix": "10.9999", "split": "EXAM", "doi": "10.1234/a", "pmcid": "PMC9"})
    assert any("10.1234/a is in both" in t for t in check_no_leak(twice))

    fitted_too = copy.deepcopy(clean)
    fitted_too["papers"].append({"prefix": "10.3390", "split": "EXAM", "doi": "10.3390/x", "pmcid": "PMC8"})
    assert any("fitted_prefixes" in t for t in check_no_leak(fitted_too))


# ---- the interval, and what it is clustered on ---------------------------------------------


def _fake(prefix: str, n: int, right: int, split: str = "VAL") -> list[Landing]:
    out = []
    for i in range(n):
        ok = i < right
        out.append(Landing(paper=f"{prefix}/{i}", prefix=prefix, split=split, familiar=False,
                           paper_type="research", words=100, xml_lane="methods",
                           pdf_lane="methods" if ok else "results", asserted=True, correct=ok))
    return out


def test_the_interval_is_clustered_on_the_publisher_not_the_paragraph():
    """Ten publishers that read perfectly and one that reads nothing right. Resampling
    paragraphs would call that a narrow interval; resampling publishers must not, because the
    next publisher could be the bad one."""
    rows: list[Landing] = []
    for i in range(10):
        rows += _fake(f"10.100{i}", 20, 20)
    rows += _fake("10.2000", 20, 0)
    ci = bootstrap(rows, precision_of, draws=500, seed=1)
    assert ci["publishers"] == 11
    assert ci["lo"] < ci["point"] <= ci["hi"]
    assert ci["lo"] < 0.92  # the bad publisher is drawn more than once in some resamples


def test_the_interval_is_reproducible_and_moves_with_the_seed_only_a_little():
    rows = [r for i in range(8) for r in _fake(f"10.30{i}", 25, 24)]
    a = bootstrap(rows, precision_of, draws=400, seed=7)
    b = bootstrap(rows, precision_of, draws=400, seed=7)
    assert a == b  # a number in a report has to come back the same


def test_a_single_publisher_gives_an_interval_that_says_so():
    ci = bootstrap(_fake("10.1", 30, 29), precision_of, draws=200, seed=2)
    assert ci["publishers"] == 1 and ci["lo"] == ci["hi"] == ci["point"]


# ---- the reporting shapes ------------------------------------------------------------------


def test_the_macro_average_is_not_dragged_by_one_large_publisher():
    rows = _fake("10.big", 1000, 1000) + _fake("10.small", 10, 0)
    got = precision_and_coverage(rows)
    assert got["precision"] > 0.98  # micro: the big publisher swamps it
    assert got["precision_macro"] == 0.5  # macro: two publishers, one of which fails entirely


def test_by_lane_and_confusion_name_where_the_wrong_assertions_went():
    rows = _rows()
    broken = [Landing(**{**r.__dict__, "pdf_lane": "results", "asserted": True,
                         "correct": r.xml_lane == "results"})
              for r in rows if r.xml_lane == "methods"] + [r for r in rows if r.xml_lane != "methods"]
    lanes = by_lane(broken)
    assert lanes["methods"]["precision"] == 0.0
    assert confusion(broken)[0][0] == "methods -> results"


def test_the_risk_coverage_curve_rises_as_the_bar_rises():
    rows = _fake("10.a", 50, 40)
    # a score that happens to rank the right ones first: the curve must then start at 1.0
    curve = risk_coverage(rows, score=lambda r: 1.0 if r.correct else 0.0, steps=5)
    assert curve[0]["precision"] == 1.0
    assert curve[-1]["precision"] == pytest.approx(0.8)
    assert curve[0]["coverage"] < curve[-1]["coverage"]


def test_sealed_is_redacted_and_reserve_is_not_scored_at_all():
    report = {"precision": 0.99, "papers_detail": [{"key": "a"}], "worst": ["b"]}
    assert redact("DEV", report) == report
    # FITTED is the old pair libraries, every publisher of which the reader was built on: it is
    # the other half of T3 and nothing is held back from it. An hour of scoring was thrown away
    # once because it was not on this list.
    assert redact("FITTED", report) == report
    assert redact("VAL", report) == report
    sealed = redact("SEALED", report)
    assert "papers_detail" not in sealed and "worst" not in sealed
    assert sealed["precision"] == 0.99 and "aggregates only" in sealed["redacted"]
    with pytest.raises(SplitViolation):
        redact("RESERVE", report)
