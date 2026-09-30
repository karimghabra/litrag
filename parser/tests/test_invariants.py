"""The thirteen invariants, each shown firing and shown staying quiet.

A check that has never been seen to fail is not evidence, and a check that has never been seen
to *pass* is worse — it is a gate that will be turned off the first week it is on. So each one
here gets a reading it should accept and a mutation of that same reading it should reject.

Nothing in this file prices an invariant. Precision against the witness is not a property of the
code and cannot be asserted in a unit test; it is measured on DEV and reported in
`campaign/reports/phase4.md`. What is asserted here is that the check says what it means: it
fires where the fault is, it names a location a repair could act on, and it is `n/a` rather than
`pass` when it has nothing to go on.
"""

import json
from pathlib import Path

import pytest

from litrag_parser import invariants as inv
from litrag_parser.invariants import Context, check, summarize
from litrag_parser.tree import Node, Page, Tree, build_tree

FIXTURES = Path(__file__).parent / "fixtures"

BODY = ("The compressive modulus of each scaffold rose steadily with the crosslinker "
        "concentration across every group tested, and the difference was significant.")
MORE = ("Tendon tissue was harvested, decellularised and sectioned before any mechanical "
        "measurement was attempted on the resulting constructs in this work.")


def _para(node_id: str, text: str, role: str, parent: str = "s", page: int = 1,
          type: str = "paragraph", bbox: list[float] | None = None) -> Node:
    return Node(node_id=node_id, parent=parent, ordinal=0, depth=2, type=type, label="text",
                level=None, role=role, heading=None, ancestry=["H"], text=text, page=page,
                bbox=bbox, self_ref=None)


def _section(node_id: str, heading: str, role: str, children: list[Node], *, page: int = 1,
             label: str = "section_header", depth: int = 1) -> Node:
    for c in children:
        c.parent = node_id
    return Node(node_id=node_id, parent="root", ordinal=0, depth=depth, type="section",
                label=label, level=depth, role=role, heading=heading, ancestry=[], text="",
                page=page, bbox=None, self_ref=None, children=children)


def _tree(sections: list[Node], *, pages: int = 1) -> Tree:
    root = Node(node_id="root", parent=None, ordinal=0, depth=0, type="document",
                label="document", level=None, role="other", heading=None, ancestry=[], text="",
                page=None, bbox=None, self_ref=None, children=sections)
    for i, n in enumerate(_walk(root)):
        n.ordinal = i
    return Tree(title="A paper", pages=[Page(page_no=p + 1, width=612.0, height=792.0)
                                        for p in range(pages)],
                root=root, roles={}, has_methods=True)


def _walk(node: Node):
    yield node
    for c in node.children:
        yield from _walk(c)


def _result(tree: Tree, code: str, **kw):
    got = check(tree, only=[code], **kw)
    assert len(got) == 1
    return got[0]


# ---- the frame -----------------------------------------------------------------------------


def test_every_invariant_in_the_register_answers():
    tree = _tree([_section("s", "1 Introduction", "introduction", [_para("p1", BODY, "introduction")])])
    got = check(tree)
    assert [r.code for r in got] == [i.code for i in inv.REGISTER]
    assert all(r.status in ("pass", "fail", "n/a") for r in got)


def test_not_applicable_is_not_a_pass():
    """A paper with no reference list has not satisfied I4. Counting it as satisfied is how a
    corpus average comes to mean nothing, so `summarize` keeps the two apart."""
    tree = _tree([_section("s", "Discussion", "discussion", [_para("p1", BODY, "discussion")])])
    got = summarize(check(tree))
    assert "I4" in got["not_applicable"]
    assert "I4" not in got["failed"]
    assert got["checked"] == len([r for r in check(tree) if r.status != "n/a"])


def test_a_check_that_raises_loses_only_itself(monkeypatch):
    """Thirteen checks over a corpus will meet a paper that breaks one of them. Losing the other
    twelve to it would be the silent failure this campaign spent a phase removing."""
    import dataclasses

    def boom(_ctx):
        raise ValueError("a paper I cannot read")

    broken = dataclasses.replace(inv.REGISTER[4], fn=boom)
    monkeypatch.setattr(inv, "REGISTER", inv.REGISTER[:4] + (broken,) + inv.REGISTER[5:])
    tree = _tree([_section("s", "1 Introduction", "introduction", [_para("p1", BODY, "introduction")])])
    got = check(tree)
    bad = next(r for r in got if r.code == broken.code)
    assert bad.status == "n/a" and "ValueError" in bad.reason
    assert len(got) == len(inv.REGISTER)


def test_every_invariant_starts_advisory():
    """Only a measurement promotes one, and the measurement lives in the phase report. A stance
    changed here without a number beside it is the thing this field exists to prevent."""
    assert {i.stance for i in inv.REGISTER} == {"advisory"}


# ---- I1 conservation -----------------------------------------------------------------------


def test_i1_is_not_applicable_without_the_pdf():
    tree = _tree([_section("s", "Methods", "methods", [_para("p1", BODY, "methods")])])
    got = _result(tree, "I1")
    assert got.status == "n/a" and "no PDF" in got.reason


def test_i1_fails_below_the_floor_and_carries_the_count(monkeypatch):
    tree = _tree([_section("s", "Methods", "methods", [_para("p1", BODY, "methods")])])
    monkeypatch.setattr(Context, "accounting", property(lambda self: {
        "layer_words": 5000, "in_a_node": 4700, "in_a_dropped_record": 100,
        "unaccounted": 200, "accounted": 0.96}))
    got = _result(tree, "I1", pdf_path=Path("nowhere.pdf"))
    assert got.failed and got.violations[0].detail["unaccounted"] == 200
    assert got.violations[0].severity == "error"

    monkeypatch.setattr(Context, "accounting", property(lambda self: {
        "layer_words": 5000, "unaccounted": 2, "accounted": 0.9996}))
    assert _result(tree, "I1", pdf_path=Path("nowhere.pdf")).status == "pass"


# ---- I2 no text twice ----------------------------------------------------------------------


def test_i2_fires_at_the_second_copy_not_the_first():
    tree = _tree([_section("s", "Results", "results", [
        _para("p1", BODY, "results"), _para("p2", MORE, "results"), _para("p3", BODY, "results")])])
    got = _result(tree, "I2")
    assert got.failed
    assert [v.node_id for v in got.violations] == ["p3"]  # the copy, not the original
    assert got.violations[0].severity == "error"


def test_i2_passes_on_prose_that_does_not_repeat():
    tree = _tree([_section("s", "Results", "results", [
        _para("p1", BODY, "results"), _para("p2", MORE, "results")])])
    assert _result(tree, "I2").status == "pass"


# ---- I3 heading numbering ------------------------------------------------------------------


def _numbered(headings: list[str]) -> Tree:
    return _tree([_section(f"s{i}", h, "methods", [_para(f"p{i}", BODY, "methods")])
                  for i, h in enumerate(headings)])


def test_i3_names_the_number_that_is_missing():
    """"3 is missing between 2 and 4" is a place to send a repair; "the numbering is odd" is not."""
    got = _result(_numbered(["1 Introduction", "2 Methods", "4 Discussion"]), "I3")
    assert got.failed
    v = got.violations[0]
    assert v.detail["missing"] == ["3"] and v.detail["kind"] == "gap"
    assert v.node_id == "s2" and v.severity == "error"


def test_i3_accepts_numbering_that_runs_on():
    got = _result(_numbered(["1 Introduction", "2 Methods", "2.1 Cells", "2.2 Assay", "3 Results"]), "I3")
    assert got.status == "pass"


def test_i3_catches_a_subsection_whose_parent_was_dropped():
    """The commonest cause of a whole lane landing in the wrong place is a top-level heading the
    layout model fused with the paragraph under it. Its children are still numbered."""
    got = _result(_numbered(["1 Introduction", "2.1 Cells", "2.2 Assay"]), "I3")
    assert got.failed
    assert any(v.detail.get("kind") == "orphan" and v.detail["missing"] == "2"
               for v in got.violations)


def test_i3_says_nothing_about_a_paper_that_does_not_number():
    got = _result(_numbered(["Introduction", "Methods", "Results"]), "I3")
    assert got.status == "n/a"


# ---- I4, I5 the reference list and what points at it ---------------------------------------


def _with_refs(entries: list[str], body: str = BODY) -> Tree:
    return _tree([
        _section("s", "Results", "results", [_para("p1", body, "results")]),
        _section("r", "References", "references",
                 [_para(f"e{i}", t, "references", parent="r", type="list_item")
                  for i, t in enumerate(entries)]),
    ])


ENTRIES = [f"{i}. Lin S, Patrawalla N, Gu L. A study of collagen number {i}. J Biomech. 20{10 + i};4:1."
           for i in range(1, 9)]


def test_i4_accepts_a_list_that_enumerates():
    assert _result(_with_refs(ENTRIES), "I4").status == "pass"


def test_i4_names_the_entry_that_is_missing():
    got = _result(_with_refs(ENTRIES[:3] + ENTRIES[4:]), "I4")
    assert got.failed
    v = next(v for v in got.violations if v.detail.get("kind") == "gap")
    assert v.detail["expected"] == 4 and v.node_id


def test_i4_catches_a_lane_that_holds_something_other_than_references():
    got = _result(_with_refs([f"A paragraph of ordinary prose, the {i}th of its kind here." for i in range(1, 9)]), "I4")
    assert got.failed
    assert any(v.detail.get("kind") == "not-references" for v in got.violations)


def test_i5_fires_where_the_text_cites_past_the_end_of_the_list():
    got = _result(_with_refs(ENTRIES, body=BODY + " This was shown before [42]."), "I5")
    assert got.failed
    v = got.violations[0]
    assert v.node_id == "p1" and v.detail["over"] == [42]


def test_i5_is_quiet_where_every_marker_resolves():
    assert _result(_with_refs(ENTRIES, body=BODY + " This was shown before [3,5]."), "I5").status == "pass"


def test_i5_says_nothing_about_a_paper_that_cites_by_name():
    got = _result(_with_refs(ENTRIES, body=BODY + " This was shown by Lin and Gu (2019)."), "I5")
    assert got.status == "n/a"


# ---- I6 figures and captions ---------------------------------------------------------------


def _with_figures(nums: list[int], body: str = BODY) -> Tree:
    figs = []
    for n in nums:
        cap = Node(node_id=f"c{n}", parent=f"f{n}", ordinal=0, depth=2, type="caption",
                   label="caption", level=None, role="other", heading=None, ancestry=[],
                   text=f"Figure {n}. The scaffold after crosslinking.", page=1, bbox=None,
                   self_ref=None)
        figs.append(Node(node_id=f"f{n}", parent="s", ordinal=0, depth=2, type="picture",
                         label="picture", level=None, role="other", heading=None, ancestry=[],
                         text="", page=1, bbox=None, self_ref=None, children=[cap]))
    return _tree([_section("s", "Results", "results", [_para("p1", body, "results"), *figs])])


def test_i6_accepts_figures_that_enumerate_and_resolve():
    assert _result(_with_figures([1, 2, 3], BODY + " See Figure 2."), "I6").status == "pass"


def test_i6_names_the_caption_that_is_missing():
    got = _result(_with_figures([1, 3]), "I6")
    assert got.failed
    v = next(v for v in got.violations if v.detail.get("kind") == "caption-gap")
    assert v.detail["missing"] == [2]


def test_i6_catches_a_call_that_names_no_caption():
    got = _result(_with_figures([1, 2], BODY + " See Figure 7."), "I6")
    assert got.failed
    assert any(v.detail.get("kind") == "dangling-call" and v.node_id == "p1" for v in got.violations)


def test_i6_catches_a_caption_hanging_on_nothing():
    tree = _with_figures([1])
    section = tree.root.children[0]
    cap = section.children[1].children[0]
    section.children[1].children = []
    cap.parent = "s"
    section.children.append(cap)
    got = _result(tree, "I6")
    assert got.failed
    assert any(v.detail.get("kind") == "orphan-caption" for v in got.violations)


# ---- I7 the type's contract ------------------------------------------------------------------


def _long(role: str, node_id: str) -> Node:
    return _para(node_id, " ".join([BODY, MORE] * 4), role)


def test_i7_is_not_applicable_without_a_type():
    tree = _tree([_section("s", "Methods", "methods", [_long("methods", "p1")])])
    assert _result(tree, "I7").status == "n/a"


def test_i7_names_the_lane_a_research_paper_is_missing():
    tree = _tree([
        _section("a", "Introduction", "introduction", [_long("introduction", "p1")]),
        _section("b", "Methods", "methods", [_long("methods", "p2")]),
        _section("c", "Results", "results", [_long("results", "p3")]),
    ])
    got = _result(tree, "I7", kind={"type": "research"})
    assert got.failed
    assert [v.detail["lane"] for v in got.violations if v.detail["kind"] == "missing-lane"] == ["discussion"]


def test_i7_catches_one_lane_swallowing_the_paper():
    """A heading the reader never found leaves the whole body in the lane above it."""
    tree = _tree([_section("b", "Methods", "methods", [_long("methods", f"p{i}") for i in range(6)])])
    got = _result(tree, "I7", kind={"type": "protocol"})
    assert got.failed
    assert any(v.detail["kind"] == "fat-lane" and v.detail["lane"] == "methods" for v in got.violations)


# ---- I8 lane order ---------------------------------------------------------------------------


def _ordered(lanes: list[str]) -> Tree:
    return _tree([_section(f"s{i}", lane.title(), lane, [_para(f"p{i}", BODY, lane)])
                  for i, lane in enumerate(lanes)])


def test_i8_accepts_the_usual_order():
    got = _result(_ordered(["abstract", "introduction", "methods", "results", "discussion"]), "I8")
    assert got.status == "pass"


def test_i8_accepts_methods_last_because_journals_print_it():
    """A house style is not an error. Nature's research papers put the methods after the
    discussion, and a check that called that a fault would be a check keyed to a publisher."""
    got = _result(_ordered(["abstract", "introduction", "results", "discussion", "methods"]), "I8")
    assert got.status == "pass"


def test_i8_names_the_lane_that_breaks_the_order():
    got = _result(_ordered(["abstract", "discussion", "methods", "introduction", "results"]), "I8")
    assert got.failed
    v = got.violations[0]
    assert v.detail["kind"] == "out-of-order" and v.node_id


def test_i8_catches_a_lane_stranded_after_the_references():
    got = _result(_ordered(["abstract", "introduction", "methods", "results", "references", "discussion"]), "I8")
    assert got.failed
    assert got.violations[0].detail["kind"] == "after-the-tail"


# ---- I9 paragraph integrity --------------------------------------------------------------------


def test_i9_fires_on_a_paragraph_cut_mid_sentence_and_offers_somewhere_to_join():
    cut = " ".join([BODY, MORE])[:-30]
    tree = _tree([_section("s", "Results", "results", [
        _para("p1", cut, "results"), _para("p2", MORE + " " + BODY, "results")])])
    got = _result(tree, "I9")
    assert got.failed
    v = next(v for v in got.violations if v.detail["kind"] == "unterminated")
    assert v.node_id == "p1" and v.detail["join"] == "p2"


def test_i9_is_quiet_on_paragraphs_that_open_and_close_like_paragraphs():
    tree = _tree([_section("s", "Results", "results", [
        _para("p1", " ".join([BODY, MORE]), "results"),
        _para("p2", " ".join([MORE, BODY]), "results")])])
    assert _result(tree, "I9").status == "pass"


def test_i9_fires_on_a_paragraph_that_starts_in_lower_case():
    tree = _tree([_section("s", "Results", "results", [
        _para("p1", " ".join([BODY, MORE]), "results"),
        _para("p2", "where each modulus is the mean of a triplicate " + MORE, "results")])])
    got = _result(tree, "I9")
    v = next(v for v in got.violations if v.detail["kind"] == "lowercase-start")
    assert v.node_id == "p2" and v.detail["join"] == "p1"


# ---- I10 furniture in the body -------------------------------------------------------------


def test_i10_catches_a_running_head_read_as_prose():
    head = "Micromachines 2024, 15, 851 — a journal of applied engineering research"
    tree = _tree([_section("s", "Results", "results", [
        _para("p1", BODY, "results"),
        *[_para(f"h{p}", head, "results", page=p) for p in (1, 2, 3)]])], pages=4)
    got = _result(tree, "I10")
    assert got.failed
    assert {v.node_id for v in got.violations} == {"h1", "h2", "h3"}
    assert got.violations[0].detail["pages"] == [1, 2, 3]


def test_i10_leaves_a_line_that_appears_twice_alone():
    """Prose does repeat itself across a paper; three pages is the bar the reader's own
    geometric furniture rule uses, and it is right every time on both columns of the corpus."""
    head = "Micromachines 2024, 15, 851 — a journal of applied engineering research"
    tree = _tree([_section("s", "Results", "results", [
        _para("p1", BODY, "results"),
        *[_para(f"h{p}", head, "results", page=p) for p in (1, 2)]])], pages=4)
    assert _result(tree, "I10").status == "pass"


def test_i10_is_not_applicable_to_a_short_paper():
    tree = _tree([_section("s", "Results", "results", [_para("p1", BODY, "results")])], pages=2)
    assert _result(tree, "I10").status == "n/a"


# ---- I11 geometry ----------------------------------------------------------------------------


def test_i11_catches_a_block_read_before_one_above_it_in_the_same_column():
    boxes = [(60.0, 100.0, 280.0, 300.0), (60.0, 500.0, 280.0, 700.0), (60.0, 320.0, 280.0, 480.0)]
    kids = [_para(f"p{i}", BODY, "results", bbox=[l, t, r, b])
            for i, (l, t, r, b) in enumerate(boxes)]
    kids.append(_para("p3", MORE, "results", bbox=[330.0, 100.0, 550.0, 300.0]))
    got = _result(_tree([_section("s", "Results", "results", kids)]), "I11")
    assert got.failed
    v = next(v for v in got.violations if v.detail["kind"] == "inversion")
    assert v.node_id == "p2" and v.detail["after"] == "p1" and v.detail["column"] == "left"


def test_i11_accepts_a_column_read_down_the_page():
    boxes = [(60.0, 100.0, 280.0, 300.0), (60.0, 320.0, 280.0, 480.0), (60.0, 500.0, 280.0, 700.0)]
    kids = [_para(f"p{i}", BODY, "results", bbox=[l, t, r, b])
            for i, (l, t, r, b) in enumerate(boxes)]
    kids.append(_para("p3", MORE, "results", bbox=[330.0, 100.0, 550.0, 300.0]))
    assert _result(_tree([_section("s", "Results", "results", kids)]), "I11").status == "pass"


# ---- I12 style classes -------------------------------------------------------------------------


def test_i12_is_not_applicable_without_the_pdf():
    tree = _tree([_section("s", "Results", "results", [_para("p1", BODY, "results")])])
    got = _result(tree, "I12")
    assert got.status == "n/a" and "does not record how text was set" in got.reason


# ---- I13 headings are headings ------------------------------------------------------------------


def test_i13_accepts_a_heading_that_behaves_like_one():
    tree = _tree([_section("s", "2 Materials and Methods", "methods", [_para("p1", BODY, "methods")])])
    assert _result(tree, "I13").status == "pass"


def test_i13_catches_a_heading_with_its_first_sentence_fused_on():
    tree = _tree([_section("s", "Results " + BODY, "results", [_para("p1", MORE, "results")])])
    got = _result(tree, "I13")
    assert got.failed
    assert any(v.detail["kind"] in ("fused", "long") for v in got.violations)


def test_i13_catches_a_heading_with_nothing_under_it():
    tree = _tree([_section("s", "3 Discussion", "discussion", [])])
    got = _result(tree, "I13")
    assert got.failed
    assert any(v.detail["kind"] == "empty" for v in got.violations)


def test_i13_leaves_a_heading_the_reader_built_alone():
    """A heading the reader built from a paper that printed none is labelled `built` and is not
    the paper's; holding it to the paper's conventions would price the reader's own repair."""
    tree = _tree([_section("s", "Introduction " + BODY, "introduction",
                           [_para("p1", MORE, "introduction")], label="built")])
    assert _result(tree, "I13").status == "n/a"


# ---- on a real paper ---------------------------------------------------------------------------


def test_the_fixture_paper_is_checked_end_to_end():
    """Every check answers on a real reading, and the answers are locations a person can open."""
    tree = build_tree(json.loads((FIXTURES / "PMC11278924.docling.json").read_text("utf-8")), "k")
    got = check(tree)
    ids = {n.node_id for n in tree.walk()}
    for r in got:
        for v in r.violations:
            assert v.code == r.code
            assert not v.node_id or v.node_id in ids, (r.code, v.node_id)
            assert v.severity in ("error", "warn", "info")
    assert summarize(got)["violations"] > 0  # a real paper is not perfect
    assert "I8" not in summarize(got)["failed"]  # and this one's lanes do run in order
