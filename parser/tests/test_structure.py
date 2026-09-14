"""Lanes from content where the headings are silent: a paper without headings gets them
built, in its own order, marked as the reader's; a section whose heading names nothing
takes the lane its paragraphs are clearly of, when that lane has a shape of its own; a
heading that names a lane is never overridden, only noted; and every decision replays
from the store without the embedder."""

import json
from pathlib import Path

from litrag_parser import lanes, meaning
from litrag_parser.audit import audit_tree
from litrag_parser.harness import compare
from litrag_parser.structure import viterbi
from litrag_parser.tree import build_tree

FIXTURES = Path(__file__).parent / "fixtures"

INTRO = "Osteoarthritis is the most common joint disease worldwide and a leading cause of disability in older adults. Despite decades of research no disease-modifying treatment is available and management is limited to symptom control."
METHODS = "Type I collagen was extracted from rat tail tendons as previously described. Tendons were dissolved in 0.02 M acetic acid at 4 °C for 48 h, centrifuged at 10,000 g for 30 min, and the supernatant was lyophilised. Statistical analysis was performed with one-way ANOVA."
RESULTS = "The compressive modulus increased from 12 ± 3 kPa to 48 ± 6 kPa as the crosslinker concentration rose from 0.5 to 2 wt% (Figure 3a, p < 0.01). The swelling ratio decreased correspondingly (Table 1) and cell number was significantly higher on the crosslinked scaffolds."
DISCUSSION = "Our findings demonstrate that the crosslinked scaffolds support cell growth while providing mechanical properties in the range of native cartilage. Several limitations should be acknowledged and further studies in large animal models are warranted."
ENTRY = "[{n}] Smith JA, Lee CD, Brown EF. Collagen crosslinking in tendon repair. J Biomed Mater Res A. 2019;107(4):812-821."
ABSTRACT = "We report a crosslinked collagen scaffold for tendon repair and measure its stiffness, swelling and cell viability over seven days in vitro, finding that a short crosslinker stiffens the network without loss of porosity and that cells grow on it as well as on the control, which suggests a route to load-bearing scaffolds."


def _doc(texts, pages=(1, 2)):
    items = [{"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "level": 1 if label == "section_header" else None, "prov": [{"page_no": page, "bbox": {"l": 50, "t": 700 - 12 * i, "r": 500, "b": 690 - 12 * i, "coord_origin": "BOTTOMLEFT"}}]} for i, (label, text, page) in enumerate(texts)]
    return {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items, "pictures": [], "tables": [], "groups": [], "pages": {str(p): {"page_no": p, "size": {"width": 600, "height": 850}} for p in pages}}


def _vary(text, n):
    return f"{text} (as seen in sample {n} of the series, with the same outcome.)"


def _headingless():
    body = [("text", _vary(METHODS, n), 1) for n in range(3)] + [("text", _vary(RESULTS, n), 1) for n in range(3)] + [("text", _vary(DISCUSSION, n), 2) for n in range(3)] + [("text", ENTRY.format(n=n), 2) for n in range(1, 9)]
    return _doc([("title", "A crosslinked collagen scaffold for tendon repair", 1), ("text", "John A. Smith 1 , Maria García 2 , Wei Zhang 1,*", 1), ("text", ABSTRACT, 1)] + body)


def test_a_paper_without_headings_gets_them_built_in_its_own_order(fake_oracle):
    tree = build_tree(_headingless(), "k")
    tops = [(n.heading, n.role, n.label) for n in tree.root.children if n.type == "section"]
    # the reference list was found by its entries' shape before any heading was built; the rest are built
    assert tops == [("Front matter", "other", "section_header"), ("Abstract", "abstract", "section_header"), ("Methods", "methods", "built"), ("Results", "results", "built"), ("Discussion", "discussion", "built"), ("References", "references", "section_header")]
    assert tree.has_methods and tree.repairs.get("built_headings") == 3 and tree.repairs.get("inferred_references") == 8
    assert [n.role for n in tree.walk() if n.type == "paragraph"] == ["abstract"] + ["methods"] * 3 + ["results"] * 3 + ["discussion"] * 3 + ["references"] * 8
    kinds = {f.kind for f in audit_tree(tree)}
    assert "built-heading" in kinds
    # the same paper, with the embedder gone: the store replays the headings
    fake_oracle._embed = lambda texts: (_ for _ in ()).throw(AssertionError("asked the embedder"))
    again = build_tree(_headingless(), "k")
    assert again.to_dict() == tree.to_dict()


def test_a_paper_with_any_heading_after_its_front_matter_is_left_alone(fake_oracle):
    doc = _headingless()
    doc["texts"].insert(2, {"self_ref": "#/texts/h", "parent": {"$ref": "#/body"}, "children": [], "label": "section_header", "text": "Experimental", "level": 1, "prov": [{"page_no": 1, "bbox": {"l": 50, "t": 690, "r": 500, "b": 680, "coord_origin": "BOTTOMLEFT"}}]})
    doc["body"]["children"].insert(2, {"$ref": "#/texts/h"})
    tree = build_tree(doc, "k")
    assert not any(n.label == "built" for n in tree.walk()) and "built_headings" not in tree.repairs


def test_a_run_the_scorer_is_unsure_of_gets_an_untitled_heading_and_stays_other(fake_oracle, monkeypatch):
    hedged = "Then the matter was considered from several angles by the group over the following weeks without any particular outcome being recorded at that time."
    body = [("text", _vary(METHODS, n), 1) for n in range(3)] + [("text", f"{hedged} Round {n}.", 1) for n in range(3)] + [("text", ENTRY.format(n=n), 2) for n in range(1, 9)]
    doc = _doc([("title", "A crosslinked collagen scaffold for tendon repair", 1), ("text", ABSTRACT, 1)] + body)
    tree = build_tree(doc, "k")
    tops = [(n.heading, n.role) for n in tree.root.children if n.type == "section"]
    assert tops == [("Abstract", "abstract"), ("Methods", "methods"), ("(untitled section)", "other"), ("References", "references")]


def test_a_section_whose_heading_names_nothing_takes_the_lane_its_paragraphs_are_of(fake_oracle):
    doc = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "1 Introduction", 1), ("text", _vary(INTRO, 0), 1), ("text", _vary(INTRO, 1), 1), ("text", _vary(INTRO, 2), 1),
        ("section_header", "2 Scaffold fabrication and testing", 1), ("text", _vary(METHODS, 0), 1), ("text", _vary(METHODS, 1), 1), ("text", _vary(METHODS, 2), 1),
        ("section_header", "3 Applications in orthopaedics", 2), ("text", _vary(DISCUSSION, 0), 2), ("text", _vary(DISCUSSION, 1), 2), ("text", _vary(DISCUSSION, 2), 2),
        ("section_header", "4 Results", 2), ("text", _vary(METHODS, 3), 2), ("text", _vary(METHODS, 4), 2), ("text", _vary(METHODS, 5), 2),
    ])
    tree = build_tree(doc, "k")
    lanes_ = {n.heading: n.role for n in tree.root.children if n.type == "section"}
    assert lanes_["2 Scaffold fabrication and testing"] == "methods"  # a lane with a shape of its own, taken from the paragraphs
    assert lanes_["3 Applications in orthopaedics"] == "other"  # discussion-like prose under a topical heading: noted, not taken
    assert lanes_["4 Results"] == "results"  # the heading names a lane; the paragraphs' disagreement is a note
    assert all(n.role == "methods" for n in tree.walk() if n.type == "paragraph" and "2 Scaffold" in n.ancestry)
    assert tree.repairs.get("laned") == 1 and tree.repairs.get("lane_disagreement") == 1
    assert sorted(n["kind"] for n in tree.notes) == ["lane-disagreement", "lane-suggested"]
    assert {f.kind for f in audit_tree(tree)} >= {"lane-disagreement", "lane-suggested"}
    assert tree.roles["methods"] == 4  # the section and its three paragraphs, recounted
    fake_oracle._embed = lambda texts: (_ for _ in ()).throw(AssertionError("asked the embedder"))
    assert build_tree(doc, "k").to_dict() == tree.to_dict()


def test_the_embedder_going_away_mid_paper_leaves_the_counts_true(fake_oracle, monkeypatch):
    doc = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "2 Scaffold fabrication and testing", 1), ("text", _vary(METHODS, 0), 1), ("text", _vary(METHODS, 1), 1), ("text", _vary(METHODS, 2), 1),
        ("section_header", "3 Applications in orthopaedics", 2), ("text", _vary(DISCUSSION, 0), 2), ("text", _vary(DISCUSSION, 1), 2), ("text", _vary(DISCUSSION, 2), 2),
    ])
    real = fake_oracle._embed
    monkeypatch.setattr(fake_oracle, "_embed", lambda texts: None if any("Our findings" in t and "as seen in sample" in t for t in texts) else real(texts))  # gone by the second section
    tree = build_tree(doc, "k")
    assert tree.roles.get("methods") == 4 and tree.has_methods  # what was laned before the outage is counted
    assert sum(1 for n in tree.walk() if n.role == "methods") == tree.roles["methods"]


def test_a_title_docling_called_a_heading_does_not_count_as_one(fake_oracle):
    doc = _headingless()
    doc["texts"][0]["label"] = "section_header"  # Docling's usual word for the big first-page line
    tree = build_tree(doc, "k")
    assert [n.heading for n in tree.root.children if n.type == "section" and n.label == "built"] == ["Methods", "Results", "Discussion"]
    assert tree.title == "A crosslinked collagen scaffold for tendon repair"


def test_the_position_prior_tips_and_never_decides():
    kind = meaning.Kind("toy", {}, 0.5, 0.08, centroids={"a": [1.0, 0.0], "b": [0.0, 1.0]}, position={"a": [0.9] + [0.1 / 9] * 9, "b": [0.01] + [0.11] * 9}, embedder="x")
    assert kind.prior("a", 0.0) == 0.1  # nine times likelier than uniform, clamped
    assert kind.prior("b", 0.0) == -0.1  # a tenth as likely, clamped
    assert kind.prior("a", 0.95) < 0 and kind.prior("a", None) == 0.0 and kind.prior("zzz", 0.5) == 0.0


def test_centroids_from_another_embedder_fall_silent_and_so_does_a_missing_data_file(tmp_path, monkeypatch):
    kind = meaning.Kind("block", {}, 0.5, 0.08, centroids={"a": [1.0, 0.0]}, embedder="someone-else")
    o = meaning.Oracle(None, model="nomic-embed-text")
    o.register(kind)
    assert o.kinds["block"].silent and o.nearest("block", "anything at all").name == "other" and o.scores("block", ["x"]) is None
    monkeypatch.setattr(meaning, "DATA", tmp_path / "nowhere")
    assert meaning.block_kind().silent


def test_a_paper_whose_title_was_not_found_still_gets_its_headings(fake_oracle):
    doc = _headingless()
    doc["texts"][0]["label"] = "text"
    doc["texts"][0]["text"] = "Collagen scaffold repair"  # three words of plain text: not a title candidate
    tree = build_tree(doc, "k")
    assert [n.heading for n in tree.root.children if n.type == "section" and n.label == "built"] == ["Methods", "Results", "Discussion"]


CITING = "Tendon injuries are among the most common musculoskeletal problems and heal slowly [1,2], which is why scaffolds have been studied for decades in this field of work, with mixed results [3]."


def test_an_introduction_orphaned_before_its_heading_is_given_one():
    # no abstract heading, a long first-page abstract, then the introduction's opening paragraphs before the first known heading
    doc = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("text", "John A. Smith 1 , Maria García 2 , Wei Zhang 1,*", 1),
        ("text", ABSTRACT, 1),
        ("text", CITING, 1),
        ("text", "Collagen is the main constituent of tendon and has been the material of choice for its repair for many years now.", 1),
        ("section_header", "2 Materials and methods", 1), ("text", _vary(METHODS, 0), 1),
        ("section_header", "3 Results", 2), ("text", _vary(RESULTS, 0), 2),
    ])
    tree = build_tree(doc, "k")
    tops = [(n.heading, n.role, n.label, len(n.children)) for n in tree.root.children if n.type == "section"]
    assert tops == [("Front matter", "other", "section_header", 1), ("Abstract", "abstract", "section_header", 1), ("Introduction", "introduction", "built", 2), ("2 Materials and methods", "methods", "section_header", 1), ("3 Results", "results", "section_header", 1)]
    assert tree.repairs.get("built_headings") == 1
    assert [n.role for n in tree.walk() if n.type == "paragraph"] == ["abstract", "introduction", "introduction", "methods", "results"]


def test_an_abstract_section_stops_where_the_paragraphs_start_citing():
    doc = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "Abstract", 1), ("text", ABSTRACT, 1),
        ("text", "Keywords: collagen; tendon; scaffold; crosslinking", 1),
        ("text", CITING, 1),
        ("section_header", "2 Materials and methods", 1), ("text", _vary(METHODS, 0), 1),
    ])
    tree = build_tree(doc, "k")
    tops = [(n.heading, n.role, n.label) for n in tree.root.children if n.type == "section"]
    assert tops == [("Abstract", "abstract", "section_header"), ("Front matter", "other", "section_header"), ("Introduction", "introduction", "built"), ("2 Materials and methods", "methods", "section_header")]
    abstract = next(n for n in tree.root.children if n.role == "abstract")
    assert [c.text[:9] for c in abstract.children] == ["We report"]
    # a structured abstract's second paragraph cites nothing and stays
    doc2 = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "Abstract", 1), ("text", ABSTRACT, 1), ("text", "Conclusions: " + ABSTRACT, 1),
        ("section_header", "1 Introduction", 1), ("text", CITING, 1),
    ])
    tree2 = build_tree(doc2, "k")
    assert len(next(n for n in tree2.root.children if n.role == "abstract").children) == 2 and "built_headings" not in tree2.repairs


def test_viterbi_pays_to_switch_and_keeps_references_last():
    m = {"introduction": 0.5, "methods": 0.7, "results": 0.5, "results-discussion": 0.5, "discussion": 0.5, "references": 0.4, "back": 0.4}
    r = {**m, "methods": 0.5, "results": 0.7}
    ref = {**m, "methods": 0.5, "references": 0.8}
    assert viterbi([m, m, m, r, r, r]) == ["methods"] * 3 + ["results"] * 3
    r2 = {**m, "methods": 0.5, "results": 0.6}
    assert viterbi([m, m, r2, m, m], switch_cost=0.06) == ["methods"] * 5  # one block alone cannot pay for two switches
    assert viterbi([m, m, r, m, m], switch_cost=0.06)[2] == "results"  # unless its preference is worth more than both
    m9 = {**m, "methods": 0.9}
    assert viterbi([m, ref, ref, m9, m9]) == ["methods"] * 5  # nothing but back matter follows a reference list, so it cannot open in the middle
    assert viterbi([m, m, ref, ref])[-2:] == ["references", "references"]
    bk = {**m, "methods": 0.4, "back": 0.9}
    assert viterbi([m, m, ref, ref, bk, bk])[-2:] == ["back", "back"]  # declarations after the list are fine
    assert viterbi([]) == []


def test_a_fixture_paper_is_the_same_tree_with_and_without_the_oracle_where_its_headings_decide(fake_oracle):
    doc = json.loads((FIXTURES / "PMC3258128.docling.json").read_text("utf-8"))
    with_oracle = build_tree(doc, "k")
    lanes._active = None
    without = build_tree(doc, "k")
    assert [(n.heading, n.role) for n in with_oracle.root.children if n.type == "section" and n.role != "other"] == [(n.heading, n.role) for n in without.root.children if n.type == "section" and n.role != "other"]
    assert sum(v["asked"] for v in fake_oracle.summary()["kinds"].values()) > 0  # the oracle was consulted, and changed no lane a heading gave


def test_a_lane_lost_is_a_named_section_in_the_harness():
    before = [{"key": "k", "format": "pdf", "title": "t", "title_ok": True, "has_methods": True, "errors": 0, "citations": 10, "lanes": [["2 Fabrication", "methods"], ["3 Uses", "other"]]}]
    after = [{"key": "k", "format": "pdf", "title": "t", "title_ok": True, "has_methods": True, "errors": 0, "citations": 10, "error_kinds": {}, "lanes": [["2 Fabrication", "other"], ["3 Uses", "results"]]}]
    diff = compare(after, before)
    assert diff["lane_lost"] == ["k  '2 Fabrication': methods → other"] and diff["lane_gained"] == ["k  '3 Uses': other → results"]
