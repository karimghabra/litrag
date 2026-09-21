"""The abstract stops at the first paragraph that cites and is not its first.

Found by attributing every wrong lane on the campaign's novel publishers to the route that
named it: the vocabulary — the most confident route, and 89.5 per cent of all assertions — is
the *least* precise of the three at 0.8914, against the embedder's 0.9882, and its errors are
almost all one shape. The heading reads "Abstract" and is read right; what is wrong is where the
section *ends*.

Priced on the paragraphs the reading files under an abstract, before a line of it was written:

    it cites                                   DEV 0.907   fitted 0.938
    it cites and is under 150 words            DEV 0.947   fitted 1.000
    it cites and is not the first paragraph    DEV 32/32   fitted 40/40
    position alone                             DEV 0.757   fitted 0.451
    length alone                               DEV 0.836   fitted 0.553

The last two are why the conjunction is the rule: a citation mark discriminates and the position
guards. On the 199 tuned pairs it moved three papers up and none down.

The post-pass is exercised directly rather than through `build_tree`, because on a small
synthetic document `structure.build_headings` reaches the same answer first and the test would
pass while measuring nothing.
"""

import json
from pathlib import Path

from litrag_parser.tree import Node, Page, Tree, _abstract_ends_where_it_cites, build_tree

FIXTURES = Path(__file__).parent / "fixtures"

LEAD = "This study asked whether a crosslinked collagen scaffold can bridge a tendon gap."
CITING = "Tendon injuries are common and heal slowly [1], which is why scaffolds are studied."
PLAIN = "The compressive modulus rose steadily with the crosslinker concentration in every group."


def _para(node_id: str, text: str, role: str) -> Node:
    return Node(node_id=node_id, parent="s", ordinal=0, depth=2, type="paragraph",
                label="text", level=None, role=role, heading=None, ancestry=["Abstract"],
                text=text, page=1, bbox=None, self_ref=None)


def _tree(paragraphs, with_intro: bool = False) -> Tree:
    """A document whose abstract section holds the given paragraphs."""
    abstract = Node(node_id="s", parent="root", ordinal=0, depth=1, type="section",
                    label="section_header", level=1, role="abstract", heading="Abstract",
                    ancestry=[], text="", page=1, bbox=None, self_ref=None,
                    children=[_para(f"p{i}", t, "abstract") for i, t in enumerate(paragraphs)])
    children = [abstract]
    if with_intro:
        children.append(Node(node_id="i", parent="root", ordinal=1, depth=1, type="section",
                             label="section_header", level=1, role="introduction",
                             heading="1 Introduction", ancestry=[], text="", page=1,
                             bbox=None, self_ref=None, children=[]))
    root = Node(node_id="root", parent=None, ordinal=0, depth=0, type="document",
                label="document", level=None, role="other", heading=None, ancestry=[],
                text="", page=None, bbox=None, self_ref=None, children=children)
    return Tree(title="t", pages=[Page(page_no=1, width=612.0, height=792.0)], root=root,
                roles={}, has_methods=False)


def _roles(tree: Tree) -> dict[str, str]:
    return {(n.text or "")[:24]: n.role for n in tree.walk() if n.type == "paragraph"}


def test_a_citing_paragraph_after_the_first_ends_the_abstract():
    tree = _tree([LEAD, CITING, PLAIN])
    repairs: dict[str, int] = {}
    _abstract_ends_where_it_cites(tree, repairs)
    roles = _roles(tree)
    assert roles[LEAD[:24]] == "abstract"  # the abstract's own lead stays
    assert roles[CITING[:24]] == "introduction"  # the citation ends it
    assert roles[PLAIN[:24]] == "introduction"  # and everything after goes with it
    assert repairs["abstract_ended_at_a_citation"] == 2


def test_the_first_paragraph_is_exempt():
    """A structured abstract's lead can carry a trial registration, or name the paper it
    comments on. Position alone prices at 0.451 on the fitted corpora; the first paragraph is
    where that noise lives."""
    tree = _tree([CITING, PLAIN])
    repairs: dict[str, int] = {}
    _abstract_ends_where_it_cites(tree, repairs)
    assert set(_roles(tree).values()) == {"abstract"}
    assert repairs == {}


def test_an_abstract_that_never_cites_is_left_whole():
    tree = _tree([LEAD, PLAIN])
    repairs: dict[str, int] = {}
    _abstract_ends_where_it_cites(tree, repairs)
    assert set(_roles(tree).values()) == {"abstract"}
    assert repairs == {}


def test_a_one_paragraph_abstract_is_never_cut():
    tree = _tree([CITING])
    repairs: dict[str, int] = {}
    _abstract_ends_where_it_cites(tree, repairs)
    assert set(_roles(tree).values()) == {"abstract"}
    assert repairs == {}


def test_the_paragraphs_join_the_introduction_the_paper_already_has():
    tree = _tree([LEAD, CITING, PLAIN], with_intro=True)
    _abstract_ends_where_it_cites(tree, {})
    intros = [n for n in tree.walk() if n.type == "section" and n.role == "introduction"]
    assert len(intros) == 1  # one introduction, not a second built beside it
    assert [(c.text or "")[:24] for c in intros[0].children] == [CITING[:24], PLAIN[:24]]


def test_an_introduction_is_built_when_the_paper_has_none():
    tree = _tree([LEAD, CITING])
    _abstract_ends_where_it_cites(tree, {})
    intros = [n for n in tree.walk() if n.type == "section" and n.role == "introduction"]
    assert len(intros) == 1 and intros[0].heading == "Introduction"
    assert [(c.text or "")[:24] for c in intros[0].children] == [CITING[:24]]


def test_the_switch_turns_it_off(monkeypatch):
    import importlib

    import litrag_parser.tree as tree_mod

    monkeypatch.setenv("LITRAG_ABSTRACT_ENDS", "off")
    importlib.reload(tree_mod)
    try:
        assert not tree_mod.ABSTRACT_ENDS
        tree = _tree([LEAD, CITING])
        repairs: dict[str, int] = {}
        tree_mod._abstract_ends_where_it_cites(tree, repairs)
        assert set(_roles(tree).values()) == {"abstract"}  # as it was before the rule
        assert repairs == {}
    finally:
        monkeypatch.delenv("LITRAG_ABSTRACT_ENDS", raising=False)
        importlib.reload(tree_mod)


def test_the_fixture_paper_is_unchanged():
    """The rule must cost nothing where the reading was already right: on the 199 tuned pairs it
    moved three papers up and none down."""
    pdf = build_tree(json.loads((FIXTURES / "PMC11278924.docling.json").read_text("utf-8")), "k")
    assert pdf.repairs.get("abstract_ended_at_a_citation") in (None, 0)
