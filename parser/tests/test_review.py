"""The reading read back by a model, and the one thing it may change: a lane where the reader's own
rules said nothing, in a paper that reads as research. Everything else is written down. No model is
asked here — the answer is handed in, as it comes back from the store on a rebuild."""

import sqlite3

import pytest

from litrag_parser import review
from litrag_parser.tree import build_tree

METHODS = "Type I collagen was extracted from rat tail tendons, dissolved in 0.02 M acetic acid at 4 °C for 48 h, centrifuged at 10,000 g and lyophilised."
RESULTS = "The compressive modulus increased from 12 kPa to 48 kPa as the crosslinker concentration rose, and the swelling ratio decreased correspondingly."
INTRO = "Tendon injuries are among the most common musculoskeletal problems and heal slowly [1,2], which is why scaffolds have been studied for decades."


def _doc(texts, pages=(1, 2)):
    items = [{"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text,
              "level": 1 if label == "section_header" else None,
              "prov": [{"page_no": page, "bbox": {"l": 50, "t": 700 - 20 * i, "r": 500, "b": 690 - 20 * i, "coord_origin": "BOTTOMLEFT"}}]}
             for i, (label, text, page) in enumerate(texts)]
    return {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items,
            "pictures": [], "tables": [], "groups": [], "pages": {str(p): {"page_no": p, "size": {"width": 600, "height": 850}} for p in pages}}


def _paper():
    # a section whose heading names no lane, holding methods prose: the silence the pass may fill
    return _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "1 Introduction", 1), ("text", INTRO, 1),
        ("section_header", "2 Scaffold fabrication and testing", 1), ("text", METHODS, 1),
        ("section_header", "3 Results", 2), ("text", RESULTS, 2),
    ])


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def _answer(monkeypatch, repairs):
    """The model's answer, handed in instead of asked for."""
    monkeypatch.setattr(review, "ask", lambda log, **kw: {"content": repairs, "seconds": 0.1, "prompt_tokens": 10, "answer_tokens": 5, "num_ctx": 8192})


def test_the_log_shows_every_section_its_lane_and_where_the_lane_came_from():
    tree = build_tree(_paper(), "k")
    log, sections, paragraphs = review.reading_log(tree)
    assert "THE SECTIONS" in log and "THE PARAGRAPHS" in log
    assert "lane methods | by its own heading" not in log  # "2 Scaffold fabrication" names no lane
    assert "by nothing: the heading names no lane" in log
    assert [s.heading for s in sections] == ["1 Introduction", "2 Scaffold fabrication and testing", "3 Results"]
    assert f"[p{len(paragraphs)}]" in log and "in S3" in log


def test_a_lane_is_filled_where_the_rules_said_nothing(monkeypatch, conn):
    monkeypatch.setenv("LITRAG_REVIEW", "on")
    _answer(monkeypatch, '[{"op": "relabel", "section": "S2", "lane": "methods", "why": "the paragraphs are methods"}]')
    tree = build_tree(_paper(), "k")
    out = review.judge(tree, conn, "k", paper_type="research")
    assert out["relabel"] == 1 and out["noted"] == 0
    section = next(n for n in tree.walk() if n.heading == "2 Scaffold fabrication and testing")
    assert section.role == "methods" and all(c.role == "methods" for c in section.children)
    assert tree.roles["methods"] == 2 and tree.has_methods


def test_the_same_answer_changes_nothing_in_a_review(monkeypatch, conn):
    # a review's sections are topical and its XML lanes them `other`: measured twice, at a cost
    monkeypatch.setenv("LITRAG_REVIEW", "on")
    _answer(monkeypatch, '[{"op": "relabel", "section": "S2", "lane": "methods", "why": "the paragraphs are methods"}]')
    tree = build_tree(_paper(), "k")
    out = review.judge(tree, conn, "k", paper_type="review")
    assert out["relabel"] == 0 and out["noted"] == 1
    assert next(n for n in tree.walk() if n.heading == "2 Scaffold fabrication and testing").role == "other"
    assert tree.notes[0]["kind"] == "review-relabel" and "reads as a review" in tree.notes[0]["message"]


def test_a_rule_that_fired_is_never_overruled(monkeypatch, conn):
    monkeypatch.setenv("LITRAG_REVIEW", "on")
    _answer(monkeypatch, '[{"op": "relabel", "section": "S3", "lane": "discussion", "why": "reads as discussion"},'
                         ' {"op": "relabel", "section": "S2", "lane": "abstract", "why": "reads as an abstract"}]')
    tree = build_tree(_paper(), "k")
    out = review.judge(tree, conn, "k", paper_type="research")
    assert out["relabel"] == 0 and out["noted"] == 2  # a heading that names its lane, and a lane the order decides
    assert next(n for n in tree.walk() if n.heading == "3 Results").role == "results"
    assert [n["kind"] for n in tree.notes] == ["review-relabel", "review-relabel"]
    assert "a rule stands" in tree.notes[0]["message"]


def test_notes_only_asks_and_applies_nothing(monkeypatch, conn):
    monkeypatch.setenv("LITRAG_REVIEW", "notes")
    _answer(monkeypatch, '[{"op": "relabel", "section": "S2", "lane": "methods", "why": "methods"}]')
    tree = build_tree(_paper(), "k")
    out = review.judge(tree, conn, "k", paper_type="research")
    assert out["relabel"] == 0 and out["noted"] == 1
    assert next(n for n in tree.walk() if n.heading == "2 Scaffold fabrication and testing").role == "other"


def test_the_row_is_replayed_and_no_model_is_asked_again(monkeypatch, conn):
    monkeypatch.setenv("LITRAG_REVIEW", "on")
    _answer(monkeypatch, '[{"op": "relabel", "section": "S2", "lane": "methods", "why": "methods"}]')
    first = review.judge(build_tree(_paper(), "k"), conn, "k", paper_type="research")
    assert first["asked"] is True
    monkeypatch.setattr(review, "ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked the model again")))
    tree = build_tree(_paper(), "k")
    again = review.judge(tree, conn, "k", paper_type="research")
    assert again["asked"] is False and again["relabel"] == 1
    assert next(n for n in tree.walk() if n.heading == "2 Scaffold fabrication and testing").role == "methods"
    # and with the pass switched off the stored answer changes nothing
    monkeypatch.setenv("LITRAG_REVIEW", "off")
    quiet = build_tree(_paper(), "k")
    review.judge(quiet, conn, "k", paper_type="research")
    assert next(n for n in quiet.walk() if n.heading == "2 Scaffold fabrication and testing").role == "other"


def test_an_xml_is_never_judged(monkeypatch, conn):
    monkeypatch.setenv("LITRAG_REVIEW", "on")
    _answer(monkeypatch, "[]")
    doc = _paper()
    doc["pages"] = {}  # a JATS document has no pages
    out = review.judge(build_tree(doc, "k"), conn, "k", paper_type="research")
    assert out["asked"] is False and out["skipped"].startswith("an XML")


def test_an_answer_the_reader_cannot_use_is_dropped_whole_or_in_part():
    tree = build_tree(_paper(), "k")
    _, sections, paragraphs = review.reading_log(tree)
    assert review.parse("not an answer", sections, paragraphs) is None
    assert review.parse("[]", sections, paragraphs) == []
    out = review.parse('[{"op": "relabel", "section": "S9", "lane": "methods"},'
                       ' {"op": "relabel", "section": "S2", "lane": "chemistry"},'
                       ' {"op": "dissolve", "section": "S2"},'
                       ' {"op": "split", "section": "S2", "after": "p99", "title": "x"},'
                       ' {"op": "relabel", "section": "S2", "lane": "methods", "why": "ok"}]', sections, paragraphs)
    assert out == [{"op": "relabel", "section": 2, "why": "ok", "lane": "methods"}]  # a section that exists, a lane that is one
    fenced = review.parse('```json\n[{"op": "not_a_section", "section": "S2", "why": "a banner"}]\n```', sections, paragraphs)
    assert fenced == [{"op": "not_a_section", "section": 2, "why": "a banner"}]
