"""The outline judge: what the model is shown, what its answer is read as, and what is taken
from it — lanes where the rules were silent, boundaries as built headings, disagreements as
notes, never the depth — with the model faked, and its rows replayed."""

import json
import sqlite3

from litrag_parser import outline
from litrag_parser.outline import apply, judge, paragraphs_of, parse, prompt_text
from litrag_parser.tree import build_tree

from test_pairs import DISCUSSION, INTRO, METHODS, RESULTS, RESULTS_2
from test_structure import _doc


def _review():
    body = [("title", "Collagen scaffolds for tendon repair: a review", 1), ("section_header", "Introduction", 1), ("text", INTRO, 1)]
    for heading, text in (("Tendon biology", METHODS), ("Growth factors in tendon healing", RESULTS), ("Scaffolds in the clinic", RESULTS_2), ("Remaining problems for the field", DISCUSSION)):
        body += [("section_header", heading, 1), ("text", text, 1)]
    return build_tree(_doc(body), "k")


def test_the_model_sees_headings_marked_and_paragraphs_numbered_without_the_references_or_the_front_matter():
    from test_structure import ENTRY

    tree = build_tree(_doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1), ("text", "John A. Smith 1 , Maria García 2 , Wei Zhang 1,*", 1),
        ("section_header", "1 Introduction", 1), ("text", INTRO, 1),
        ("section_header", "2 Methods", 1), ("text", METHODS, 1),
        ("section_header", "References", 2), *[("list_item", ENTRY.format(n=n), 2) for n in range(1, 4)],
    ]), "k")
    text, paragraphs = prompt_text(tree)
    assert [n.text[:20] for n in paragraphs] == [INTRO[:20], METHODS[:20]]
    assert "# 1 Introduction" in text and "[p1] " + INTRO[:30] in text and "[p2] " + METHODS[:30] in text
    assert "Smith" not in text and "References" not in text and ENTRY[:20].format(n=1) not in text
    kept = outline.BUDGET_WORDS, outline.EXCERPT_WORDS
    outline.BUDGET_WORDS, outline.EXCERPT_WORDS = 10, 8
    try:
        short, _ = prompt_text(tree)
        assert short.count(" …") == 2 and len(short) < len(text)  # a long paper as excerpts
    finally:
        outline.BUDGET_WORDS, outline.EXCERPT_WORDS = kept


def test_reference_entries_the_reader_took_for_prose_are_not_shown_to_the_model():
    from litrag_parser.outline import looks_like_references

    entries = "Smith JA, Lee CD. Collagen crosslinking in tendon repair. J Biomed Mater Res A. 2019;107(4):812-821. Garcia M, Zhang W. Genipin and the mechanics of collagen threads. Acta Biomater. 2021;128:100-110. doi:10.1016/j.actbio.2021.01.001. Brown EF, et al. Tendon healing. Nat Rev Rheumatol. 2020;16:1-12."
    assert looks_like_references(entries)
    assert not looks_like_references(METHODS) and not looks_like_references("The 2019 and 2020 cohorts were compared with the 2021 cohort after adjustment for age, and the difference held in every year of follow-up that was examined in the study.")


def test_the_answer_is_read_leniently_and_cleaned():
    content = '```json\n[{"title": "Abstract", "level": 1, "lane": "abstract", "first_paragraph": 1}, {"title": "Conclusions", "level": "1", "lane": "Conclusion", "first_paragraph": 9}, {"title": "Late", "lane": "results", "first_paragraph": 99}, {"title": "Twice", "lane": "methods", "first_paragraph": 1}, {"title": "Odd", "lane": "Materials and methods", "level": 7, "first_paragraph": 4, "printed": false}, "junk"]\n```'
    got = parse(content, 12)
    assert [(e["first_paragraph"], e["lane"], e["level"], e["printed"]) for e in got] == [(1, "abstract", 1, True), (4, "methods", 3, False), (9, "discussion", 1, True)]
    assert parse("no outline here", 12) is None and parse("[]", 12) == [] and parse('{"a": 1}', 12) is None
    cut = '[{"title": "Abstract", "level": 1, "lane": "abstract", "first_paragraph": 1}, {"title": "Methods", "level": 1, "lane": "methods", "first_paragraph": 3}, {"title": "Res'
    assert [e["first_paragraph"] for e in parse(cut, 12)] == [1, 3]  # an answer cut short keeps the sections it finished
    assert [e["lane"] for e in parse("Sure, here it is:\n[{\"title\": \"A\", \"lane\": \"results\", \"first_paragraph\": 2}]\nHope this helps.", 12)] == ["results"]


def test_a_lane_goes_where_the_rules_gave_none_and_a_named_heading_keeps_its_own():
    tree = _review()
    text, paragraphs = prompt_text(tree)
    assert {n.role for n in tree.walk() if n.type == "paragraph"} == {"introduction"}  # the reader nested every topical section under the introduction
    answer = [
        {"title": "Introduction", "printed": True, "level": 1, "lane": "introduction", "first_paragraph": 1},
        {"title": "Tendon biology", "printed": True, "level": 1, "lane": "other", "first_paragraph": 2},
        {"title": "Growth factors in tendon healing", "printed": True, "level": 1, "lane": "other", "first_paragraph": 3},
        {"title": "Scaffolds in the clinic", "printed": True, "level": 1, "lane": "results", "first_paragraph": 4},
        {"title": "Remaining problems for the field", "printed": True, "level": 1, "lane": "discussion", "first_paragraph": 5},
    ]
    repairs: dict = {}
    done = apply(tree, answer, paragraphs, repairs, "fake")
    roles = {n.heading: n.role for n in tree.walk() if n.type == "section"}
    # a review's section the model puts outside the introduction the reader nested it under is a topical section,
    # `other`, whatever the model calls it: a lane is a heading's word, and these headings name none
    assert roles["Introduction"] == "introduction" and roles["Tendon biology"] == "other" and roles["Growth factors in tendon healing"] == "other"
    assert roles["Scaffolds in the clinic"] == "other" and roles["Remaining problems for the field"] == "other"
    assert done["lanes"] == 4 and repairs["outline_lanes"] == 4 and done["built"] == 0
    assert tree.roles["other"] >= 8 and not tree.roles.get("results") and tree.roles["introduction"] == 2  # recounted: the heading and its one paragraph
    # in a research paper a subsection is part of its section, whatever the model calls it
    research = _review()
    _, paragraphs = prompt_text(research)
    done = apply(research, answer, paragraphs, {}, "fake", paper_type="research")
    assert done["lanes"] == 0 and {n.role for n in research.walk() if n.type == "paragraph"} == {"introduction"}
    # a heading the reader built that names a lane is the reader's rule, not its guess: the model's word is a note
    built = _review()
    intro = next(n for n in built.walk() if n.heading == "Introduction")
    intro.label = "built"
    _, paragraphs = prompt_text(built)
    done = apply(built, [{"title": "Introduction", "printed": False, "level": 1, "lane": "results-discussion", "first_paragraph": 1}], paragraphs, {}, "fake")
    assert done["lanes"] == 0 and done["disagreements"] == 1 and "the built heading names introduction" in built.notes[0]["message"] and intro.role == "introduction"
    # under the abstract nothing of the body belongs: the sections the reader left there leave it — in a research
    # paper with the model's lane, in a review as topical sections
    from test_structure import ABSTRACT

    def _under_abstract():
        return build_tree(_doc([("title", "A paper", 1), ("section_header", "Abstract", 1), ("text", ABSTRACT, 1), ("section_header", "Tendon biology", 1), ("text", METHODS, 1), ("section_header", "Scaffolds in the clinic", 1), ("text", RESULTS_2, 1)]), "k")

    left = [{"title": "Abstract", "printed": True, "level": 1, "lane": "abstract", "first_paragraph": 1}, {"title": "Tendon biology", "printed": True, "level": 1, "lane": "methods", "first_paragraph": 2}, {"title": "Scaffolds in the clinic", "printed": True, "level": 1, "lane": "results", "first_paragraph": 3}]
    flat = _under_abstract()
    _, paragraphs = prompt_text(flat)
    assert {n.role for n in flat.walk() if n.type == "section" and n.heading in ("Tendon biology", "Scaffolds in the clinic")} == {"abstract"}
    done = apply(flat, left, paragraphs, {}, "fake", paper_type="research")
    assert done["lanes"] == 2 and next(n.role for n in flat.walk() if n.text == METHODS) == "methods" and next(n.role for n in flat.walk() if n.text == RESULTS_2) == "results"
    assert flat.roles["results"] == 2 and flat.roles["abstract"] == 2  # the abstract is one heading and one paragraph again
    review = _under_abstract()
    _, paragraphs = prompt_text(review)
    done = apply(review, left, paragraphs, {}, "fake", paper_type="review")
    assert done["lanes"] == 2 and {n.role for n in review.walk() if n.text in (METHODS, RESULTS_2)} == {"other"} and review.roles["abstract"] == 2
    # a heading that names its lane keeps it, the disagreement is a note
    named = build_tree(_doc([("title", "A paper", 1), ("section_header", "Introduction", 1), ("text", INTRO, 1), ("section_header", "Methods", 1), ("text", METHODS, 1)]), "k")
    _, paragraphs = prompt_text(named)
    done = apply(named, [{"title": "Methods", "printed": True, "level": 1, "lane": "results", "first_paragraph": 2}], paragraphs, {}, "fake")
    assert done["lanes"] == 0 and done["disagreements"] == 1 and named.notes[0]["kind"] == "outline-disagreement" and "the rule's word stands" in named.notes[0]["message"]
    assert next(n.role for n in named.walk() if n.heading == "Methods") == "methods"
    # a numbered subsection keeps the lane its numbering put it under: the authors' structure, not the reader's guess
    numbered = build_tree(_doc([("title", "A review", 1), ("section_header", "1. Introduction", 1), ("text", INTRO, 1), ("section_header", "1.1 Anatomy of the cornea", 1), ("text", METHODS, 1), ("section_header", "2. Conclusions", 1), ("text", DISCUSSION, 1)]), "k")
    _, paragraphs = prompt_text(numbered)
    done = apply(numbered, [{"title": "1.1 Anatomy of the cornea", "printed": True, "level": 2, "lane": "other", "first_paragraph": 2}], paragraphs, {}, "fake")
    assert done["lanes"] == 0 and next(n.role for n in numbered.walk() if n.heading == "1.1 Anatomy of the cornea") == "introduction"
    xml = build_tree(_doc([("title", "A review", 1), ("section_header", "Introduction", 1), ("text", INTRO, 1)], pages=()), "k")
    assert judge(xml, None, "k", model="fake", ask_model=False)["skipped"]  # an XML is never judged


def test_a_boundary_the_reader_missed_becomes_a_built_heading_and_no_text_moves_out_of_order():
    tree = build_tree(_doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "1 Introduction", 1), ("text", INTRO, 1),
        ("section_header", "2 Materials and methods", 1), ("text", METHODS, 1), ("text", RESULTS, 2), ("text", RESULTS_2, 2),
        ("section_header", "4 Discussion", 2), ("text", DISCUSSION, 2),
    ]), "k")
    before = [n.text for n in tree.walk() if n.type == "paragraph"]
    _, paragraphs = prompt_text(tree)
    answer = [
        {"title": "Introduction", "printed": True, "level": 1, "lane": "introduction", "first_paragraph": 1},
        {"title": "Materials and methods", "printed": True, "level": 1, "lane": "methods", "first_paragraph": 2},
        {"title": "Results", "printed": False, "level": 1, "lane": "results", "first_paragraph": 3},
        {"title": "Discussion", "printed": True, "level": 1, "lane": "discussion", "first_paragraph": 5},
    ]
    repairs: dict = {}
    done = apply(tree, answer, paragraphs, repairs, "fake")
    assert done["built"] == 1 and repairs["outline_built"] == 1
    built = next(n for n in tree.walk() if n.type == "section" and n.label == "built")
    # the run ends the methods, and "Results" names a lane as a top-level heading would: a top-level section after the methods
    assert built.heading == "Results" and built.role == "results" and built.level == 1 and built.ancestry == [] and built.parent == tree.root.node_id
    assert [c.text[:16] for c in built.children] == [RESULTS[:16], RESULTS_2[:16]] and all(c.role == "results" and c.parent == built.node_id and c.depth == built.depth + 1 for c in built.children)
    methods = next(n for n in tree.walk() if n.heading == "2 Materials and methods")
    tops = [n for n in tree.root.children if n.type == "section"]
    assert [c.ordinal for c in tree.root.children] == list(range(len(tree.root.children))) and tops[tops.index(methods) + 1] is built and [c.text for c in methods.children] == [METHODS]
    assert [n.text for n in tree.walk() if n.type == "paragraph"] == before  # every paragraph, in the same order
    assert next(n.role for n in tree.walk() if n.text == METHODS) == "methods" and tree.roles["results"] == 3  # the built heading and its two paragraphs
    assert next(n.role for n in tree.walk() if n.text == INTRO) == "introduction"


def test_a_built_heading_takes_its_lane_as_the_reader_gives_one_never_from_the_model():
    thanks = "We thank the staff of the animal facility for their care of the animals, and the imaging core for the microscopy."
    tree = build_tree(_doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "2 Materials and methods", 1), ("text", METHODS, 1), ("text", RESULTS, 1), ("text", RESULTS_2, 2), ("text", INTRO, 2),
        ("section_header", "4 Discussion", 2), ("text", DISCUSSION, 2), ("text", thanks, 2), ("section_header", "4.1 Limitations", 2), ("text", "The study was limited to one scaffold formulation and one time point.", 2),
    ]), "k")
    before = [n.text for n in tree.walk() if n.type == "paragraph"]
    _, paragraphs = prompt_text(tree)
    answer = [
        {"title": "Materials and methods", "printed": True, "level": 1, "lane": "methods", "first_paragraph": 1},
        {"title": "Scaffold mechanics", "printed": False, "level": 2, "lane": "results", "first_paragraph": 2},
        {"title": "Cell response", "printed": True, "level": 2, "lane": "results", "first_paragraph": 3},
        {"title": "Discussion", "printed": True, "level": 1, "lane": "discussion", "first_paragraph": 4},  # off by one: the reader has this heading, at the next paragraph
        {"title": "4 Discussion", "printed": True, "level": 1, "lane": "discussion", "first_paragraph": 5},
        {"title": "Acknowledgments", "printed": True, "level": 1, "lane": "results", "first_paragraph": 6},
    ]
    done = apply(tree, answer, paragraphs, {}, "fake")
    assert done["built"] == 3 and done["lanes"] == 0
    # a nested title that names back matter keeps it, as the reader's own nested "Funding" does — whatever the model said
    # (nested, since a subsection follows: it is not the end of the discussion)
    acks = next(n for n in tree.walk() if n.heading == "Acknowledgments")
    assert acks.label == "built" and acks.role == "back" and acks.ancestry == ["4 Discussion"] and [c.role for c in acks.children] == ["back"]
    methods = next(n for n in tree.walk() if n.heading == "2 Materials and methods")
    assert [(c.type, (c.heading or c.text)[:16]) for c in methods.children] == [("paragraph", METHODS[:16]), ("section", "Scaffold mechani"), ("section", "Cell response")]
    mechanics, response = methods.children[1], methods.children[2]
    assert mechanics.label == response.label == "built" and mechanics.role == response.role == "methods"  # topical titles: the lane of the section above, not the model's `results`
    assert [c.text[:16] for c in mechanics.children] == [RESULTS[:16]] and [c.text[:16] for c in response.children] == [RESULTS_2[:16], INTRO[:16]]  # the second boundary a sibling, not a section inside the first
    assert response.parent == methods.node_id and response.depth == mechanics.depth and [c.ordinal for c in methods.children] == [0, 1, 2]
    assert all(n.role == "methods" for n in tree.walk() if n.text in (RESULTS, RESULTS_2, INTRO))
    assert [n.text for n in tree.walk() if n.type == "paragraph"] == before and sum(1 for n in tree.walk() if n.heading and "discussion" in n.heading.lower()) == 1  # the heading the reader has was not built twice
    # a numbered title the reader dropped stands beside the section its numbering matches, when the paragraphs end
    # everything below it, and names its own lane there: "4. Conclusions" read into the last subsection of "3"
    named = build_tree(_doc([("title", "A paper", 1), ("section_header", "3 Results", 1), ("text", RESULTS, 1), ("section_header", "3.2 Cell response", 1), ("text", RESULTS_2, 1), ("text", DISCUSSION, 1)]), "k")
    _, paragraphs = prompt_text(named)
    done = apply(named, [{"title": "4. Conclusions", "printed": False, "level": 2, "lane": "other", "first_paragraph": 3}], paragraphs, {}, "fake")
    tops = [(n.heading, n.role, n.label, n.level) for n in named.root.children if n.type == "section" and n.heading != "Abstract"]
    assert done["built"] == 1 and tops[-2:] == [("3 Results", "results", "section_header", 1), ("4. Conclusions", "discussion", "built", 1)]
    assert next(n.role for n in named.walk() if n.text == DISCUSSION) == "discussion" and next(n.role for n in named.walk() if n.text == RESULTS_2) == "results"
    assert [n.text for n in named.walk() if n.type == "paragraph"] == [RESULTS, RESULTS_2, DISCUSSION]
    # the same title with paragraphs after it in "3.2": a subsection of "3.2", which inherits — the numbering cannot be honoured without reordering text
    inside = build_tree(_doc([("title", "A paper", 1), ("section_header", "3 Results", 1), ("text", RESULTS, 1), ("section_header", "3.2 Cell response", 1), ("text", RESULTS_2, 1), ("text", DISCUSSION, 1), ("section_header", "3.3 Histology", 1), ("text", INTRO, 1)]), "k")
    _, paragraphs = prompt_text(inside)
    done = apply(inside, [{"title": "4. Conclusions", "printed": False, "level": 2, "lane": "discussion", "first_paragraph": 3}], paragraphs, {}, "fake")
    built = next(n for n in inside.walk() if n.heading == "4. Conclusions")
    assert done["built"] == 1 and built.ancestry == ["3 Results", "3.2 Cell response"] and built.role == "results" and [n.text for n in inside.walk() if n.type == "paragraph"] == [RESULTS, RESULTS_2, DISCUSSION, INTRO]
    outline.BUILD_POLICY, kept = "never", outline.BUILD_POLICY
    try:
        assert apply(build_tree(_doc([("title", "A paper", 1), ("section_header", "2 Materials and methods", 1), ("text", METHODS, 1), ("text", DISCUSSION, 1)]), "k"), [{"title": "Conclusions", "printed": False, "level": 2, "lane": "discussion", "first_paragraph": 2}], paragraphs, {}, "fake")["built"] == 0
    finally:
        outline.BUILD_POLICY = kept


def test_the_judge_asks_once_stores_the_answer_and_replays_it(tmp_path, monkeypatch):
    calls = []

    def fake_ask(text, *, model=None, url=None, timeout=0):
        calls.append(model)
        return {"content": json.dumps([{"title": "Introduction", "printed": True, "level": 1, "lane": "introduction", "first_paragraph": 1}, {"title": "Tendon biology", "printed": True, "level": 1, "lane": "other", "first_paragraph": 2}, {"title": "Growth factors in tendon healing", "printed": True, "level": 1, "lane": "other", "first_paragraph": 3}, {"title": "Scaffolds in the clinic", "printed": True, "level": 1, "lane": "other", "first_paragraph": 4}, {"title": "Remaining problems for the field", "printed": True, "level": 1, "lane": "discussion", "first_paragraph": 5}]), "prompt_tokens": 900, "answer_tokens": 120, "seconds": 0.1}

    monkeypatch.setattr(outline, "ask", fake_ask)
    conn = sqlite3.connect(tmp_path / "store.sqlite")
    tree = _review()
    got = judge(tree, conn, "k", model="fake:1b", ask_model=True)
    assert got["asked"] and got["sections"] == 5 and got["lanes"] == 4 and calls == ["fake:1b"]
    assert conn.execute("SELECT model, prompt_tokens FROM outlines WHERE paper = 'k'").fetchone() == ("fake:1b", 900)
    again = judge(_review(), conn, "k", model="fake:1b", ask_model=False)  # a rebuild: the row, never the model
    assert calls == ["fake:1b"] and not again["asked"] and again["lanes"] == 4
    other = judge(_review(), conn, "k", model="fake:2b", ask_model=False)  # another model: another row, and none yet
    assert other["sections"] is None and calls == ["fake:1b"]
    assert not outline.enabled()  # off unless asked for
    monkeypatch.setenv("LITRAG_OUTLINE", "on")
    assert outline.enabled()
