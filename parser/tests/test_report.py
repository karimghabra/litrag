"""The front page quotes the measurement rather than remembering it.

The risk this page carries is drift: a number written into prose is right on the day it is written and
wrong the next time the measurement runs. So the prose itself — the words in the three failure cards, not
only the tables — is built from `numbers.json`. These tests change the measurement and check the page
changes with it.
"""

from __future__ import annotations

import json

from litrag_parser import report

from test_summary import NUMBERS as BASE


NUMBERS = json.loads(json.dumps(BASE))
NUMBERS["pairs"]["all"]["lane_confusion_word_counts"] = {"other → introduction": 57082, "other → methods": 24286}
NUMBERS["paper_type"]["by_source"]["cascade-shipped"]["per_type"]["other"] = {
    "truth": 4, "named": 41, "correct": 1, "precision": 0.024, "recall": 0.25,
}
NUMBERS["paper_type"]["by_source"]["cascade-shipped"]["confusion"] = {
    "review→other": 25, "editorial→other": 8, "letter→other": 5, "letter→research": 2,
}
NUMBERS["confidence"]["sure_and_wrong"] = [
    {"key": "doi:10.1371/journal.pcbi.1014552", "confidence": 1.0, "faithful": 0.5763},
    {"key": "doi:10.1186/s12888-026-08491-2", "confidence": 0.966, "faithful": 0.5847},
    {"key": "doi:10.1002/advs.201500213", "confidence": 1.0, "faithful": 0.693},
]


def test_the_headline_numbers_come_from_the_measurement():
    html = report.build(NUMBERS, publishers=69, changes=14093)
    assert "0.990" in html and "0.840" in html      # read, and filed correctly
    assert "69 publishers" in html and "14,093" in html
    assert "797 papers" in html or "797" in html


def test_the_failure_cards_quote_the_measurement_not_the_author():
    html = report.build(NUMBERS, publishers=69, changes=14093)
    # where the words actually went, read out of the pair measurement
    assert "57,082 words other → introduction" in html
    assert "24,286 words other → methods" in html
    # the bucket that loses the type decision, with its own counts
    assert "names 41 papers" in html and "right about 1" in html
    assert "25 reviews" in html and "8 editorials" in html and "5 letters" in html
    # only the papers that scored a full 1.0 are called out as such
    assert "2 papers score a full 1.0" in html
    assert "doi:10.1371/journal.pcbi.1014552 at 0.58 faithful" in html
    assert "doi:10.1186" not in html  # scored 0.966, not a full 1.0


def test_changing_the_measurement_changes_the_prose():
    moved = json.loads(json.dumps(NUMBERS))
    moved["pairs"]["all"]["lane_confusion_word_counts"] = {"results → methods": 11}
    moved["confidence"]["sure_and_wrong"] = []

    html = report.build(moved, publishers=1, changes=1)

    assert "11 words results → methods" in html
    assert "57,082" not in html
    assert "0 papers score a full 1.0" in html


def test_a_missing_measurement_leaves_a_dash_not_a_zero():
    bare = {"harness": {"by_format_corpus_wide": {"pdf": {"papers": 3}, "jats": {"papers": 4}}}}

    html = report.build(bare)

    assert "<title>" in html and "</html>" in html  # the page still renders
    assert "0.000" not in html                      # nothing was invented to fill a row
    assert report.pct(None) == "—"


def test_the_page_counts_publishers_and_changes_from_the_review_itself(tmp_path):
    review = tmp_path / "review"
    review.mkdir()
    (review / "index.json").write_text(json.dumps([
        {"key": "doi:10.1002/x", "changes": 12},
        {"key": "doi:10.1016/y", "changes": 30},
        {"key": "doi:10.1002/z", "changes": 0},
        {"key": "sha_abc", "changes": 5},
    ]), encoding="utf-8")

    assert report.publisher_count(review) == 2   # two DOI prefixes; the sha key has no publisher
    assert report.recorded_changes(review) == 47


def test_the_page_links_to_the_other_two(tmp_path):
    review, measure = tmp_path / "review", tmp_path / "measure"
    review.mkdir(), measure.mkdir()
    (review / "index.json").write_text("[]", encoding="utf-8")
    (measure / "numbers.json").write_text(json.dumps(NUMBERS), encoding="utf-8")

    report.main(["--review", str(review), "--measure", str(measure)])

    html = (review / "report.html").read_text(encoding="utf-8")
    assert 'href="index.html"' in html and 'href="summary.html"' in html
