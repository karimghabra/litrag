"""How far a reading can be trusted, from the reading alone: the signals are plain measurements
of a tree, the score takes points for the failures the PDF/XML pairs showed, and says why."""

import sqlite3

from litrag_parser.confidence import CHECKS, assess, calibrate, score, signals
from litrag_parser.store import open_store, set_confidence
from litrag_parser.tree import build_tree

from test_pairs import DISCUSSION, INTRO, METHODS, RESULTS, RESULTS_2, _paper
from test_structure import _doc

RESEARCH = {"type": "research", "source": "default"}


def test_a_clean_reading_loses_nothing_and_says_nothing():
    got = assess(_paper(), RESEARCH)
    assert got["confidence"] == 1.0 and got["reasons"] == [] and got["penalties"] == {}
    sig = got["signals"]
    assert sig["missing_lanes"] == [] and sig["repeated_share"] == 0.0 and sig["back_share"] == 0.0 and sig["largest_lane"] in ("results", "methods", "discussion", "introduction")


def test_a_review_whose_sections_became_the_introductions_children_is_not_trusted():
    # a PDF of an unnumbered review: every topical heading nests under "Introduction", and the whole body reads as introduction
    body = [("section_header", "Introduction", 1), ("text", INTRO, 1)]
    for heading, text in (("Tendon biology", METHODS), ("Growth factors in tendon healing", RESULTS), ("Scaffolds in the clinic", RESULTS_2), ("Open questions", DISCUSSION)):
        body += [("section_header", heading, 1), ("text", text, 1)]
    tree = build_tree(_doc([("title", "Collagen scaffolds for tendon repair: a review", 1), *body]), "k")
    got = assess(tree, {"type": "review", "source": "title"})
    assert got["signals"]["largest_lane"] == "introduction" and got["signals"]["largest_lane_share"] > 0.8
    assert got["confidence"] < 0.5 and "the introduction holds" in got["reasons"][0]


def test_a_research_paper_with_no_results_lane_and_text_said_twice_loses_points_for_each():
    tree = build_tree(_doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "1 Introduction", 1), ("text", INTRO, 1),
        ("section_header", "2 Materials and methods", 1), ("text", METHODS, 1), ("text", RESULTS, 1), ("text", RESULTS_2, 2),
        ("section_header", "3 Discussion", 2), ("text", DISCUSSION, 2), ("text", RESULTS_2, 2),  # the same paragraph again, a page later
    ]), "k")
    got = assess(tree, RESEARCH)
    assert got["signals"]["missing_lanes"] == ["results"] and got["signals"]["repeated_share"] > 0.1
    assert set(got["penalties"]) >= {"missing", "repeats"} and 0.2 < got["confidence"] < 0.75
    assert any("lanes a paper of this type has are missing" in r for r in got["reasons"]) and any("there twice" in r for r in got["reasons"])
    assert assess(tree, {"type": "other", "source": "record"})["penalties"].get("missing") is None  # nothing is expected of a type with no usual shape


def test_every_check_takes_nothing_below_its_limit_and_no_more_than_its_weight():
    clean = signals(_paper(), RESEARCH)
    for c in CHECKS:
        assert c.penalty(clean) == 0.0, c.name
        assert 0 < c.weight < 1 and c.worst > c.limit
    worst = {**clean, "abstract_share": 0.9, "back_share": 0.9, "refs_prose_share": 0.9, "largest_lane": "methods", "largest_lane_share": 0.99, "missing_lanes_n": 4, "repeated_share": 0.9, "unterminated_share": 0.9, "odd_headings": 40, "type_unsettled": 1}
    got = score(worst)
    assert 0 < got["confidence"] < 0.02 and len(got["reasons"]) == len(got["penalties"]) >= 8
    assert list(got["penalties"].values()) == sorted(got["penalties"].values(), reverse=True)  # the heaviest reason first
    for c in CHECKS:
        assert c.penalty(worst) <= c.weight


def test_the_score_is_a_column_and_an_old_store_grows_it(tmp_path):
    path = tmp_path / "store.sqlite"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE papers (key TEXT PRIMARY KEY, doi TEXT, pmid TEXT, pmcid TEXT, title TEXT NOT NULL, file TEXT, sha256 TEXT, format TEXT, pages INTEGER, status TEXT NOT NULL DEFAULT 'queued', error TEXT, parser TEXT, added_at TEXT NOT NULL, parsed_at TEXT, seconds REAL, has_methods INTEGER)")
    c.execute("INSERT INTO papers (key, title, added_at) VALUES ('p', 'A paper', '2026-09-17')")
    c.commit()
    c.close()
    conn = open_store(path)
    set_confidence(conn, "p", 0.62, ["the methods hold 61% of the body: a heading after them was missed"], {"methods": 0.32})
    row = conn.execute("SELECT confidence, confidence_detail FROM papers WHERE key = 'p'").fetchone()
    assert row["confidence"] == 0.62 and '"methods": 0.32' in row["confidence_detail"] and "a heading after them was missed" in row["confidence_detail"]


def test_the_calibration_reads_the_pair_records():
    clean = signals(_paper(), RESEARCH)
    broken = {**clean, "largest_lane": "introduction", "largest_lane_share": 0.95}
    records = [{"key": f"good:{i}", "faithful": 0.97, "precision": 0.98, "intrinsic": clean} for i in range(6)] + [{"key": f"bad:{i}", "faithful": 0.2, "precision": 0.99, "intrinsic": broken} for i in range(4)]
    cal = calibrate(records)
    assert cal["pairs"] == 10 and cal["well_matched"] == 6 and cal["seriously_mismatched"] == 4
    assert cal["auc_well"] == 1.0 and cal["bands"][0]["papers"] == 6 and cal["bands"][-1]["seriously_mismatched"] == 4
    assert cal["checks"]["introduction"] == {"fires": 4, "on_badly_matched": 4, "on_seriously_mismatched": 4} and cal["sure_and_wrong"] == []
