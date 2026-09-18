"""Pooling the runs is arithmetic, and arithmetic over papers is where a corpus figure goes wrong.

The trap this file guards is averaging averages: three pairings of 31, 33 and 135 papers do not weigh the
same, and neither do a 500-word letter and a 12,000-word review when the question is how many words were
misfiled. So the pooled mean is re-derived over every paper, and the landing shares are weighted by length.
"""

from __future__ import annotations

import json

from litrag_parser import numbers


def _pairs_run(records: list[dict]) -> dict:
    return {"summary": {"pairs": len(records)}, "pairs": records}


def _paper(key: str, words: int, faithful: float, same: float, intro: float, **rest) -> dict:
    return {
        "key": key, "xml_words": words, "faithful": faithful, "recall": 0.99, "precision": 0.97,
        "title_same": True, "landed": {"same lane": same, "lane introduction": intro},
        "headings": {"recall": 0.8, "precision": 0.9}, "paragraphs": {"intact": 0.9, "missing": 0.0},
        **rest,
    }


def test_pooling_three_pairings_re_averages_over_papers(tmp_path):
    # one small pairing read perfectly, one large pairing read badly: the mean must follow the papers
    (tmp_path / "pairs-small.json").write_text(json.dumps(_pairs_run([
        _paper("a", 1000, 1.0, 1.0, 0.0, pdf_library="p1", xml_library="x1"),
    ])), encoding="utf-8")
    (tmp_path / "pairs-big.json").write_text(json.dumps(_pairs_run([
        _paper(k, 1000, 0.5, 0.5, 0.5, pdf_library="p2", xml_library="x2") for k in "bcd"
    ])), encoding="utf-8")

    pooled = numbers.pairs_pooled(tmp_path)

    assert pooled["all"]["pairs"] == 4
    # averaging the two pairings' means would give 0.75; averaging the four papers gives 0.625
    assert pooled["all"]["mean"]["faithful"] == 0.625
    assert pooled["small"]["pdf_lib"] == "p1" and pooled["big"]["xml_lib"] == "x2"


def test_landing_is_weighted_by_how_long_the_paper_is(tmp_path):
    # a 9,000-word paper read perfectly and a 1,000-word paper read entirely into the introduction
    (tmp_path / "pairs-one.json").write_text(json.dumps(_pairs_run([
        _paper("long", 9000, 1.0, 1.0, 0.0, pdf_library="p", xml_library="x"),
        _paper("short", 1000, 0.0, 0.0, 1.0, pdf_library="p", xml_library="x"),
    ])), encoding="utf-8")

    pooled = numbers.pairs_pooled(tmp_path)["one"]

    assert pooled["xml_words"] == 10_000
    assert pooled["word_weighted_landing"]["same lane"] == 0.9      # not the 0.5 a per-paper mean gives
    assert pooled["word_weighted_landing_words"]["lane introduction"] == 1000


def test_a_single_pairing_is_not_given_a_pooled_all(tmp_path):
    (tmp_path / "pairs-only.json").write_text(json.dumps(_pairs_run([
        _paper("a", 100, 0.9, 0.9, 0.1, pdf_library="p", xml_library="x")
    ])), encoding="utf-8")

    assert "all" not in numbers.pairs_pooled(tmp_path)


def test_the_harness_is_pooled_by_format_not_by_library(tmp_path):
    for lib, papers in (
        ("one", [{"format": "pdf", "title_ok": True, "has_methods": True, "errors": 0, "citations": 10,
                  "dropped_lines": 2, "confidence": 0.95, "confidence_reasons": ["a lane is missing"]},
                 {"format": "pdf", "title_ok": False, "review_like": True, "errors": 1, "citations": 5,
                  "dropped_lines": 0, "confidence": 0.3, "confidence_reasons": ["a lane is missing"]}]),
        ("two", [{"format": "jats", "title_ok": True, "has_methods": True, "errors": 0, "citations": 40,
                  "dropped_lines": 0, "confidence": 1.0}]),
    ):
        (tmp_path / f"harness-{lib}.json").write_text(
            json.dumps({"summary": {"papers": len(papers)}, "papers": papers}), encoding="utf-8")

    pooled = numbers.harness_corpus(tmp_path)
    pdf, jats = pooled["by_format_corpus_wide"]["pdf"], pooled["by_format_corpus_wide"]["jats"]

    assert pooled["corpus_papers"] == 3
    assert pdf["papers"] == 2 and jats["papers"] == 1
    assert pdf["title_ok"] == {"n": 1, "of": 2, "rate": 0.5}
    assert pdf["methods_or_review"] == {"n": 2, "of": 2, "rate": 1.0}   # one has methods, one is a review
    assert pdf["clean"] == {"n": 1, "of": 2, "rate": 0.5}
    assert pdf["citations_linked"] == 15 and jats["citations_linked"] == 40
    assert pdf["confidence_bands"] == {"papers": 2, "ge_0.9": 1, "0.5_to_0.9": 0, "lt_0.5": 1}
    assert pdf["top_confidence_reasons"] == {"a lane is missing": 2}
    assert set(pooled["by_library"]) == {"one", "two"}


def test_a_run_that_is_missing_leaves_its_section_out(tmp_path):
    (tmp_path / "type.json").write_text(json.dumps({"labelled": 248}), encoding="utf-8")

    built = numbers.build(tmp_path)

    assert built["paper_type"] == {"labelled": 248}
    assert "pairs" not in built and "harness" not in built and "confidence" not in built
    assert built["run"]["commands"]["pairs"].startswith("uv run")


def test_the_file_records_what_it_was_measured_against(tmp_path):
    built = numbers.build(tmp_path, root="/scratch/root-final", embedder="nomic-embed-text, all cached")

    assert built["run"]["root"] == "/scratch/root-final"
    assert built["run"]["embedder"] == "nomic-embed-text, all cached"
    assert len(built["run"]["date"]) == 10
