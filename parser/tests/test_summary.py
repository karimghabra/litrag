"""The corpus page renders the measurements it was given, and says nothing where none was run.

The page's promise is that every number on it was written by a measurement run rather than computed on the
page. These tests hold it to that from both sides: the numbers in `numbers.json` reach the page unaltered,
and a section whose measurement is missing disappears instead of printing a zero that looks measured.
"""

from __future__ import annotations

import json
import re

from litrag_parser import summary


NUMBERS = {
    "run": {
        "date": "2026-09-17",
        "root": "<libroot>",
        "embedder": "nomic-embed-text via Ollama",
        "commands": {"harness": "python -m litrag_parser.harness --lib <lib>"},
    },
    "harness": {
        "by_format_corpus_wide": {
            "pdf": {
                "papers": 284,
                "title_ok": {"n": 281, "of": 284, "rate": 0.9894},
                "methods_or_review": {"n": 268, "of": 284, "rate": 0.9437},
                "clean": {"n": 275, "of": 284, "rate": 0.9683},
                "citations_linked": 20559,
                "dropped_sentences": 359,
                "confidence_bands": {"ge_0.9": 186, "0.5_to_0.9": 58, "lt_0.5": 40},
                "top_confidence_reasons": {"1 of the lanes a paper of this type has are missing": 13},
            },
            "jats": {
                "papers": 513,
                "title_ok": {"n": 513, "of": 513, "rate": 1.0},
                "citations_linked": 60071,
                "dropped_sentences": 0,
                "confidence_bands": {"ge_0.9": 427, "0.5_to_0.9": 65, "lt_0.5": 21},
            },
        }
    },
    "pairs": {
        "looped-ligament": {"pairs": 31, "mean": {"recall": 0.993, "faithful": 0.775, "headings.recall": 0.819}},
        "all": {
            "pairs": 199,
            "mean": {"recall": 0.990, "faithful": 0.840, "paragraphs.missing": 0.001, "headings.recall": 0.83},
            "faithful_at_least": {"0.95": 107, "0.9": 130, "0.5": 177},
        },
    },
    "paper_type": {
        "labelled": 248,
        "by_truth": {"review": 134, "research": 70},
        "stated_sources": {"agree": 358, "disagree": 19},
        "by_source": {
            "printed": {"answered": 9, "accuracy": 1.0, "per_type": {"research": {"recall": 1.0}}},
            "cascade-shipped": {"answered": 248, "accuracy": 0.786,
                                "per_type": {"letter": {"recall": 0.3}, "editorial": {"recall": 0.267}}},
        },
    },
    "headings": {
        "rows": 12964,
        "unique": 7812,
        "sample_at": "0.7/0.03",
        "grid": {
            "0.7/0.03": {"lanes": {"precision": 1.0, "recall": 0.995}, "canonical": {"precision": 0.994, "recall": 0.902}},
            "0.8/0.05": {"lanes": {"precision": 1.0, "recall": 0.918}, "canonical": {"precision": 0.996, "recall": 0.761}},
        },
        "unlabelled": {"lanes": {"other": 882, "back": 540}, "canonical": {"other": 6769}},
    },
    "confidence": {
        "pairs": 199,
        "auc_well": 0.788,
        "rank_correlation_with_faithful": 0.502,
        "bands": [
            {"confidence": "0.9 to 1", "papers": 129, "seriously_mismatched": 11, "mean_faithful": 0.939},
            {"confidence": "0 to 0.5", "papers": 30, "seriously_mismatched": 25, "mean_faithful": 0.384},
        ],
        "checks": {"methods": {"fires": 19, "on_seriously_mismatched": 13},
                   "repeats": {"fires": 21, "on_seriously_mismatched": 4}},
    },
}


def test_harness_section_shows_both_formats_and_the_counts_behind_each_rate():
    html = summary._harness_section(NUMBERS)
    assert "PDF (284)" in html and "publisher XML (513)" in html
    assert "0.989" in html and "281/284" in html  # the rate, and what it is a rate of
    assert "20,559" in html and "60,071" in html
    assert "1 of the lanes a paper of this type has are missing" in html


def test_pairs_section_carries_every_pairing_and_the_overall_column():
    html = summary._pairs_section(NUMBERS)
    assert "looped-ligament (31)" in html and "all (199)" in html
    assert "0.990" in html and "0.840" in html
    assert "107" in html and "at least 0.95 faithful" in html
    assert "where it is filed" in html  # the warning that reads the two rows together


def test_type_section_ranks_sources_by_accuracy_and_names_their_weakest_types():
    html = summary._type_section(NUMBERS)
    assert html.index("printed") < html.index("cascade-shipped")  # best first
    assert "0.786" in html and "letter 0.30" in html
    assert "358" in html and "19" in html  # stated sources agreeing and disagreeing


def test_headings_section_marks_the_shipped_threshold_and_counts_what_it_named():
    html = summary._headings_section(NUMBERS)
    assert "0.7/0.03" in html and "← shipped" in html
    assert "0.902" in html and "12,964" in html
    assert "1,422" in html and "other 882" in html  # lanes put on headings no rule had a word for


def test_confidence_section_says_the_score_is_weak_at_the_top():
    html = summary._confidence_section(NUMBERS)
    assert "0.788" in html and "0.9 to 1" in html
    assert "Trust a low score; do not trust a high one." in html
    assert "repeats" in html and "methods" in html


def test_a_measurement_that_was_never_run_leaves_no_section_behind():
    for section in (summary._harness_section, summary._pairs_section, summary._type_section,
                    summary._headings_section, summary._confidence_section):
        assert section({}) == ""


def test_a_rate_is_coloured_by_where_it_stands_and_a_missing_one_is_a_dash():
    assert "#15803d" in summary._rate(0.99)           # green where it should be
    assert "#b45309" in summary._rate(0.85)           # amber
    assert "#b91c1c" in summary._rate(0.4)            # red
    assert "—" in summary._rate(None)


def test_the_page_states_which_run_wrote_its_numbers(tmp_path):
    review, measure = tmp_path / "review", tmp_path / "measure"
    review.mkdir(), measure.mkdir()
    (review / "index.json").write_text("[]", encoding="utf-8")
    (measure / "numbers.json").write_text(json.dumps(NUMBERS), encoding="utf-8")

    html = summary.build(review, measure, [])

    assert "Where these numbers came from" in html
    assert "2026-09-17" in html and "nomic-embed-text via Ollama" in html
    assert "0.840" in html and "0.786" in html  # the measurements reached the page
    assert not re.search(r"\{[a-z_]+\}", html)  # every placeholder in the template was filled


def test_without_numbers_the_page_falls_back_to_the_runs_it_can_find(tmp_path):
    review, measure = tmp_path / "review", tmp_path / "measure"
    review.mkdir(), measure.mkdir()
    (review / "index.json").write_text("[]", encoding="utf-8")
    (measure / "pairs-looped.json").write_text(json.dumps({"summary": {"faithful": 0.775}}), encoding="utf-8")

    html = summary.build(review, measure, [])

    assert "No <code>numbers.json</code>" in html
    assert "0.775" in html
