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
NUMBERS["pairs"]["all"]["word_weighted_landing"] = {"same lane": 0.806, "lane introduction": 0.0587, "nowhere": 0.0395}
NUMBERS["pairs"]["all"]["word_weighted_landing_words"] = {"same lane": 828_470, "lane introduction": 60_321, "nowhere": 40_587}
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
    # where the words actually went, read out of the pair measurement and said as a person would
    assert "5.9% read as introduction (60,321 words)" in html
    assert "4.0% read into no section at all (40,587 words)" in html
    assert "same lane" not in html  # the part that went right is not a failure to name
    # the bucket that loses the type decision, with its own counts
    assert "names 41 papers" in html and "right about 1" in html
    assert "25 reviews" in html and "8 editorials" in html and "5 letters" in html
    # only the papers that scored a full 1.0 are called out as such
    assert "2 papers score a full 1.0" in html
    assert "doi:10.1371/journal.pcbi.1014552 at 0.58 faithful" in html
    assert "doi:10.1186" not in html  # scored 0.966, not a full 1.0


def test_changing_the_measurement_changes_the_prose():
    moved = json.loads(json.dumps(NUMBERS))
    moved["pairs"]["all"]["word_weighted_landing"] = {"same lane": 0.99, "lane methods": 0.01}
    moved["pairs"]["all"]["word_weighted_landing_words"] = {"same lane": 990, "lane methods": 11}
    moved["confidence"]["sure_and_wrong"] = []

    html = report.build(moved, publishers=1, changes=1)

    assert "1.0% read as methods (11 words)" in html
    assert "60,321" not in html
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


def test_the_page_names_the_papers_to_open_first(tmp_path):
    review = tmp_path / "review"
    review.mkdir()
    (review / "index.json").write_text(json.dumps([
        {"key": "doi:10.1/good", "title": "A paper read well", "confidence": 0.99,
         "report": "lib--good/index.html", "format": "pdf", "type": "research", "changes": 3},
        {"key": "doi:10.1/bad", "title": "A paper read badly", "confidence": 0.09,
         "report": "lib--bad/index.html", "format": "pdf", "type": "other", "changes": 27},
        {"key": "doi:10.1/failed", "failed": True, "error": "no pages"},
    ]), encoding="utf-8")

    worst = report.worst_read(review)
    assert [row["key"] for row in worst] == ["doi:10.1/bad", "doi:10.1/good"]  # lowest trust first
    assert all("confidence" in row for row in worst)  # a paper that failed has no score to sort on

    html = report.build(NUMBERS, worst=worst)
    assert "Where to look first" in html
    assert 'href="lib--bad/index.html"' in html and "9%" in html
    assert html.index("A paper read badly") < html.index("A paper read well")


def test_how_much_of_the_reader_is_visible_is_computed_not_claimed(tmp_path):
    review = tmp_path / "review"
    review.mkdir()
    (review / "index.json").write_text(json.dumps([
        {"key": "a", "changes": 40, "counted": 40, "unplaced": 0},
        {"key": "b", "changes": 20, "counted": 60, "unplaced": 40},   # a pass that counts more than it records
        {"key": "c", "failed": True, "error": "no pages"},            # never read, so it counts for nothing
    ]), encoding="utf-8")

    placed, counted, whole = report.placement(review)
    assert (placed, counted, whole) == (60, 100, 1)

    html = report.build(NUMBERS, placed=(placed, counted, whole))
    assert "60 of 100" in html and "60%" in html
    assert "1 papers have no gap at all" in html


def test_without_the_counters_the_page_makes_no_claim_about_placement():
    html = report.build(NUMBERS, placed=(0, 0, 0))
    assert "Every change, on its page" in html   # the card still explains what was built
    assert "counted modifications" not in html   # but claims no share it cannot compute
