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
    Landing, accounting, layer_words, bootstrap, by_lane, check_no_leak, confusion, coverage_of, landings,
    node_verdicts,
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


def test_conservation_counts_the_numbers_too(layer_from):
    """A paper is mostly numbers exactly where text is most likely to be lost — a table's cells,
    an axis label, a p-value, a reference year. `pairs.words` is `[a-z]{2,}` and would score a
    reading that dropped every one of them at 1.0."""
    assert layer_words("Table 2 shows 43.7 +/- 1.2 mg/L at 298 K (n = 6)") == [
        "table", "2", "shows", "43", "7", "1", "2", "mg", "l", "at", "298", "k", "n", "6"]

    pdf, _ = _trees()
    layer_from(["the modulus was 43 kPa at 298 K"])
    bare = copy.deepcopy(pdf)
    for n in bare.walk():
        n.text, n.heading, n.table = "", None, None
    bare.dropped_items = []
    bare.title = "the modulus was kPa at K"  # every word but the numbers
    got = accounting(bare, Path("x.pdf"))
    assert got["unaccounted"] == 2  # "43" and "298": the two the old tokeniser could not see
    assert got["accounted"] < 1.0


def test_precision_falls_when_two_sections_swap_their_lanes_in_the_tree():
    """The mutation is made to the **reading**, not to the rows it produces.

    An earlier version of this test rebuilt `Landing`s with `correct` computed by the test and
    then asserted that `precision_of` reported it — which checks arithmetic and leaves
    `landings()`, the only function that turns a reading into numbers, with no test that can
    fail. Here the PDF tree's own lanes are swapped and the whole pipeline is run again."""
    pdf, xml = _trees()
    before = precision_of(_rows(pdf, xml))
    assert before is not None and before > 0.9

    for n in pdf.walk():
        if n.role == "methods":
            n.role = "results"
        elif n.role == "results":
            n.role = "methods"
    after = precision_of(_rows(pdf, xml))
    assert after < 0.6 < before, (before, after)


def test_a_reading_that_lanes_everything_the_same_way_is_caught():
    """The degenerate reader — one lane for the whole paper — has full coverage and must not
    have good precision."""
    pdf, xml = _trees()
    for n in pdf.walk():
        if n.role in ("methods", "results", "discussion", "introduction", "abstract"):
            n.role = "methods"
    rows = _rows(pdf, xml)
    got = precision_and_coverage(rows)
    assert got["coverage"] > 0.9  # it asserts on nearly everything
    assert got["precision"] < 0.5  # and is wrong about most of it


def test_a_reading_that_names_nothing_has_no_precision_and_no_coverage():
    pdf, xml = _trees()
    for n in pdf.walk():
        n.role = "other"
    got = precision_and_coverage(_rows(pdf, xml))
    assert got["coverage"] == 0.0 and got["precision"] is None
    assert got["not_asserted"].get("silent", 0) > 0  # the prose is still held, just unnamed


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


def _unclustered(rows, stat, draws=500, seed=1):
    """What the interval would be if it resampled paragraphs instead of publishers — the wrong
    thing, written out so the right thing can be shown to differ from it."""
    rng = random.Random(seed)
    got = []
    for _ in range(draws):
        pick = [rows[rng.randrange(len(rows))] for _ in rows]
        v = stat(pick)
        if v is not None:
            got.append(v)
    got.sort()
    return got[max(0, int(0.025 * len(got)) - 1)], got[min(len(got) - 1, int(0.975 * len(got)))]


def test_the_interval_is_clustered_on_the_publisher_not_the_paragraph():
    """Ten publishers that read perfectly and one that reads nothing right.

    Asserting only that the interval is wide would not test the clustering: an interval over
    paragraphs is wide too, just less so. What separates them is that resampling publishers can
    draw the bad one twice — or not at all — so the clustered interval must reach materially
    further in both directions than the unclustered one. This is checked against an unclustered
    bootstrap computed here, rather than against a number chosen by hand."""
    rows: list[Landing] = []
    for i in range(10):
        rows += _fake(f"10.100{i}", 20, 20)
    rows += _fake("10.2000", 20, 0)

    ci = bootstrap(rows, precision_of, draws=500, seed=1)
    lo, hi = _unclustered(rows, precision_of, draws=500, seed=1)
    assert ci["publishers"] == 11
    assert ci["lo"] < lo, (ci, lo)  # the clustered interval reaches lower
    assert ci["hi"] >= hi           # ... and higher: some resamples have no bad publisher at all
    assert (ci["hi"] - ci["lo"]) > 2 * (hi - lo)  # and is much wider, not marginally
    assert ci["hi"] == 1.0  # the bad publisher is left out of some draws entirely


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
    sealed = redact("SEALED", {"overall": {"precision": 0.99}, "papers_detail": [{"key": "a"}],
                               "worst": ["b"], "a_key_nobody_has_thought_of_yet": ["c"]})
    assert "papers_detail" not in sealed and "worst" not in sealed
    # a whitelist, so a key added to the report later does not reach a sealed split by default
    assert "a_key_nobody_has_thought_of_yet" not in sealed
    assert sealed["overall"] == {"precision": 0.99} and "aggregates only" in sealed["redacted"]
    with pytest.raises(SplitViolation):
        redact("RESERVE", report)


def test_one_document_owning_the_weight_is_reported(tmp_path=None):
    """A micro-average over paragraphs is a weighted average and one document can own the
    weight. On DEV a conference-proceedings supplement is 15 per cent of every paragraph in the
    split and moves coverage from 0.779 to 0.675 on its own. The number was not wrong; reporting
    it without saying that was."""
    def paper(name, prefix, n, right):
        return [Landing(paper=name, prefix=prefix, split="DEV", familiar=False,
                        paper_type="research", words=100, xml_lane="methods",
                        pdf_lane="methods" if i < right else "results",
                        asserted=True, correct=i < right, where="a lane")
                for i in range(n)]

    small = [r for i in range(9) for r in paper(f"p{i}", f"10.{i}", 10, 10)]
    huge = paper("the-supplement", "10.big", 900, 0)
    got = precision_and_coverage(small + huge)
    assert got["largest_paper_share"] > 0.9  # one document is nearly the whole corpus
    assert got["precision"] < 0.1  # the micro-average is that document
    assert got["precision_median_paper"] == 1.0  # the typical paper is not
    assert got["papers"] == 10


# ---- the witness's opinion of a node, which is what a located finding is priced against -----


def test_node_verdicts_speak_only_about_blocks_the_witness_can_see():
    """A caption, a table, a heading: the XML twin has no paragraph there, so the block is
    absent from the verdicts rather than present and correct. Counting the unjudgeable as right
    would flatter every invariant priced against it."""
    pdf, xml = _trees()
    got = node_verdicts(pdf, xml)
    assert got, "the fixture pair should produce verdicts"
    kinds = {v.kind for v in got.values()}
    assert kinds <= {"paragraph", "list_item"}, kinds
    ids = {n.node_id for n in pdf.walk()}
    assert set(got) < ids  # strictly fewer blocks than the reading has


def test_a_well_read_paper_has_few_wrong_blocks():
    pdf, xml = _trees()
    got = node_verdicts(pdf, xml)
    wrong = sum(1 for v in got.values() if v.wrong)
    assert wrong / len(got) < 0.25, (wrong, len(got))


def test_swapping_two_lanes_in_the_reading_makes_those_blocks_wrong():
    """The same mutation `precision_of` is shown failing under, seen from the other side: the
    blocks themselves, which is the side an invariant fires at."""
    pdf, xml = _trees()
    before = sum(1 for v in node_verdicts(pdf, xml).values() if v.lane_wrong)
    for n in pdf.walk():
        if n.role == "methods":
            n.role = "results"
        elif n.role == "results":
            n.role = "methods"
    after = node_verdicts(pdf, xml)
    assert sum(1 for v in after.values() if v.lane_wrong) > before + 10
    assert all(v.wrong for v in after.values() if v.lane_wrong)


def test_gluing_two_paragraphs_together_is_seen_as_a_merge():
    """The two blocks glued have to be two the *witness* has as separate paragraphs.

    The first cut of this test took the first two long paragraphs of a section, and one of them
    was the fixture's front-matter citation line — text the XML twin does not have at all. Gluing
    it to its neighbour merges nothing, and the test failed for a reason that had nothing to do
    with the measure. So the blocks are chosen from the verdicts themselves."""
    pdf, xml = _trees()
    before = node_verdicts(pdf, xml)
    assert not any(v.merged for v in before.values())
    single = {nid for nid, v in before.items() if v.paragraphs == 1}
    for section in pdf.walk():
        kids = [c for c in section.children if c.node_id in single]
        if len(kids) >= 2:
            kids[0].text = kids[0].text + " " + kids[1].text
            section.children.remove(kids[1])
            break
    else:
        pytest.skip("the fixture has no section with two judgeable paragraphs")
    after = node_verdicts(pdf, xml)
    merged = [v for v in after.values() if v.merged]
    assert merged and all(v.wrong for v in merged)


def test_cutting_a_paragraph_in_two_is_seen_as_a_split():
    pdf, xml = _trees()
    before = sum(1 for v in node_verdicts(pdf, xml).values() if v.holds_a_split)
    from litrag_parser.tree import Node

    for section in pdf.walk():
        for i, c in enumerate(list(section.children)):
            if c.type == "paragraph" and len(c.text) > 600:
                half = len(c.text) // 2
                tail = Node(**{**c.__dict__, "node_id": c.node_id + "#tail",
                               "text": c.text[half:], "children": []})
                c.text = c.text[:half]
                section.children.insert(i + 1, tail)
                after = sum(1 for v in node_verdicts(pdf, xml).values() if v.holds_a_split)
                assert after > before, (before, after)
                return
    pytest.skip("the fixture has no paragraph long enough to cut")


def test_a_landing_names_the_block_it_landed_in():
    """`xml_node` says which paragraph of the witness a row is about; `pdf_node` says which
    block of the reading is answerable for it. Without the second, a finding at a node cannot be
    joined to the witness at all."""
    rows = _rows()
    landed = [r for r in rows if r.where != "nowhere"]
    assert landed and all(r.pdf_node for r in landed)
    assert all(not r.pdf_node for r in rows if r.where == "nowhere")
    ids = {n.node_id for n in _trees()[0].walk()}
    assert {r.pdf_node for r in landed} <= ids
