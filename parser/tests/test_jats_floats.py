"""A JATS file's figures and tables read where its text puts them: the floats an article keeps in
a `<floats-group>` set after the block that first cites them, labels and calls written as the
page prints them, an image of a table read as a figure, and a reading that holds fewer floats
than its file saying so. All but one test are lxml alone; one reads the bytes with Docling's
JATS backend, which asks no model and no network."""

import gc
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from lxml import etree

from litrag_parser import jats_prep, tree as tree_module
from litrag_parser.facets import normalise
from litrag_parser.jats_prep import floats_in, floats_note, name_floats, prepare_jats
from litrag_parser.tree import build_tree

from test_jats_reading import _jats

FIXTURES = Path(__file__).parent / "fixtures"

# f1 and t1 cited in one Results paragraph (the table first, the group's order kept); f2, a
# figure group, cited by a panel's id in a later one; f3 only from a list item; f4 first from
# an appendix; t9 from nowhere; and a graphical abstract in <front> that is no float of the text
FLOATS = """<?xml version="1.0" encoding="UTF-8"?>
<article><front><article-meta><title-group><article-title>Aligned collagen threads</article-title></title-group>
<abstract><p>We made threads.</p></abstract>
<fig id="ga"><label>Graphical Abstract</label><caption><p>The threads.</p></caption></fig></article-meta></front>
<body><sec><title>Introduction</title><p>Tendons heal slowly.</p></sec>
<sec><title>Results</title>
<p id="p-f1">The threads were strong (<xref ref-type="table" rid="t1">Table 1</xref>, <xref ref-type="fig" rid="f1">Figure 1</xref>).</p>
<p id="p-none">They were also long.</p>
<p id="p-f2">Cells aligned along them (<xref ref-type="fig" rid="f2b">Figure 2b</xref>; <xref ref-type="fig" rid="f1">Figure 1</xref>).</p>
<list list-type="bullet"><list-item><p>Stiffness rose with crosslinking (<xref ref-type="fig" rid="f3">Figure 3</xref>).</p></list-item></list>
<p id="p-last">Nothing else was measured.</p></sec></body>
<back><app-group><app id="app1"><title>Appendix A</title><p id="p-f4">The rig (<xref ref-type="fig" rid="f4">Figure 4</xref>).</p></app></app-group>
<ref-list><ref id="r1"><mixed-citation>Smith J. Tendons. 2019.</mixed-citation></ref></ref-list></back>
<floats-group>
<fig id="f1"><label>Figure 1</label><caption><p>Strength.</p></caption><graphic/></fig>
<table-wrap id="t1"><label>Table 1</label><caption><p>Moduli.</p></caption><table><tr><td>12</td><td>48</td></tr></table></table-wrap>
<fig-group id="f2"><fig id="f2a"><label>Figure 2a</label><caption><p>Cells, day 1.</p></caption></fig><fig id="f2b"><label>Figure 2b</label><caption><p>Cells, day 7.</p></caption></fig></fig-group>
<fig id="f3"><label>Figure 3</label><caption><p>Stiffness.</p></caption></fig>
<fig id="f4"><label>Figure 4</label><caption><p>The rig.</p></caption></fig>
<table-wrap id="t9"><label>Table 9</label><caption><p>Uncited.</p></caption><table><tr><td>1</td></tr></table></table-wrap>
</floats-group></article>"""


def _root(raw: str | bytes):
    return etree.fromstring(raw.encode("utf-8") if isinstance(raw, str) else raw)


def _by_id(root, ident):
    return next(el for el in root.iter() if isinstance(el.tag, str) and el.get("id") == ident)


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split())


def test_floats_are_set_where_the_text_first_cites_them():
    root = _root(prepare_jats(FLOATS.encode("utf-8")))
    assert root.find(".//floats-group") is None  # emptied and removed
    after = lambda ident: _by_id(root, ident).getnext()  # noqa: E731
    assert after("p-f1").get("id") == "f1" and after("f1").get("id") == "t1" and after("t1").get("id") == "p-none"  # the group's order, not the citation's
    assert after("p-f2").get("id") == "f2" and after("f2").tag == "list"  # cited by a panel's id, the whole group comes; f1 is not moved again
    assert after("f2").getnext().get("id") == "f3"  # a citation in a list item sets the figure after the list
    assert _by_id(root, "f4").getparent().get("id") == "app1" and after("p-f4").get("id") == "f4"
    last = root.find("body")[-1]
    assert last.tag == "sec" and last.findtext("title") == "(untitled section)" and [c.get("id") for c in last if c.tag != "title"] == ["t9"]
    assert _by_id(root, "ga").getparent().tag == "article-meta"  # the graphical abstract stays where it was


def test_placement_is_the_same_every_time():
    paras = "".join(
        f'<p id="p{i}">Paragraph {i}' + "".join(f' (<xref ref-type="fig" rid="f{j}">Figure {j + 1}</xref>)' for j in range(40) if (j * 7) % 15 == i) + ".</p>"
        for i in range(15))
    figs = "".join(f'<fig id="f{j}"><label>Figure {j + 1}</label><caption><p>Panel {j}.</p></caption></fig>' for j in range(40))
    raw = f'<?xml version="1.0"?><article><body><sec><title>Results</title>{paras}</sec></body><floats-group>{figs}</floats-group></article>'.encode("utf-8")
    first = prepare_jats(raw)
    gc.collect()
    assert prepare_jats(raw) == first
    sec = _root(first).find("body/sec")
    cited_in = None
    for el in sec:
        if el.tag == "p":
            cited_in, order = int(el.get("id")[1:]), []
        elif el.tag == "fig":
            j = int(el.get("id")[1:])
            assert (j * 7) % 15 == cited_in  # right after the paragraph that cites it, or after a figure that paragraph cites first
            order.append(j)
            assert order == sorted(order)  # in the group's order
    assert len(sec.findall("fig")) == 40


def test_a_range_of_calls_cites_the_floats_between_its_ends():
    # thno.124843's "Tables 1-3": no xref names Table 2, which is an image here; a figure that
    # stands between the ends in the group is not a table, and waits for its own call
    raw = ('<article><body><sec><title>1. Introduction</title><p id="p1">Reviewed before (Tables <xref ref-type="table" rid="T1">1</xref>'
           '-<xref ref-type="table" rid="T3">3</xref>).</p></sec><sec><title>2. Results</title><p id="p2">See <xref ref-type="fig" rid="F1">Figure 1</xref>.</p></sec></body>'
           '<floats-group><table-wrap id="T1"><label>Table 1</label><table><tr><td>1</td></tr></table></table-wrap>'
           '<fig id="F1"><label>Figure 1</label><caption><p>Cells.</p></caption></fig>'
           '<table-wrap id="T2"><label>Table 2</label><caption><p>An image.</p></caption><graphic/></table-wrap>'
           '<table-wrap id="T3"><label>Table 3</label><table><tr><td>3</td></tr></table></table-wrap></floats-group></article>')
    root = _root(prepare_jats(raw.encode("utf-8")))
    after = lambda ident: _by_id(root, ident).getnext()  # noqa: E731
    assert after("p1").get("id") == "T1" and after("T1").get("id") == "T2" and after("T2").get("id") == "T3"
    assert _by_id(root, "T2").tag == "fig" and after("p2").get("id") == "F1"  # placed while a table, then read as the image it is
    assert not [t for t in root.iter("title") if t.text == "(untitled section)"]


def test_a_file_with_no_body_gets_one_for_its_floats():
    raw = ('<article><front><article-meta><title-group><article-title>A letter</article-title></title-group></article-meta></front>'
           '<back><ref-list><ref id="r1"><mixed-citation>Smith J. Tendons. 2019.</mixed-citation></ref></ref-list></back>'
           '<floats-group><fig id="f1"><label>Figure 1</label><caption><p>Alone.</p></caption></fig></floats-group></article>')
    root = _root(prepare_jats(raw.encode("utf-8")))
    assert [c.tag for c in root] == ["front", "body", "back"]
    sec = _by_id(root, "f1").getparent()
    assert sec.getparent().tag == "body" and sec.findtext("title") == "(untitled section)"


# f1 cited in the body and again in an appendix; f2 first in a footnote of the back matter, which
# is no block, then in the appendix; f3 in a paragraph inside a box; a table group by its inner
# table's id and a box of the group's own beside f1; a graphic that is no float; a sub-article's own group
EDGES = """<article><body><sec><title>Results</title>
<p id="p1">Strong (<xref ref-type="fig" rid="f1">Figure 1</xref>; <xref ref-type="table" rid="t2a">Table 2a</xref>; <xref ref-type="boxed-text" rid="bx1">Box 1</xref>).</p>
<boxed-text id="box"><p id="p3">Inside a box (<xref ref-type="fig" rid="f3">Figure 3</xref>).</p></boxed-text></sec></body>
<back><fn-group><fn id="fn1"><p>First in a footnote (<xref ref-type="fig" rid="f2">Figure 2</xref>).</p></fn></fn-group>
<app-group><app id="app1"><title>Appendix A</title><p id="pa">Again (<xref ref-type="fig" rid="f1">Figure 1</xref>; <xref ref-type="fig" rid="f2">Figure 2</xref>).</p></app></app-group></back>
<floats-group>
<fig id="f1"><label>Figure 1</label><caption><p>One.</p></caption></fig>
<fig id="f2"><label>Figure 2</label><caption><p>Two.</p></caption></fig>
<fig id="f3"><label>Figure 3</label><caption><p>Three.</p></caption></fig>
<table-wrap-group id="t2"><label>Table 2</label><table-wrap id="t2a"><caption><p>(a)</p></caption><table><tr><td>1</td></tr></table></table-wrap></table-wrap-group>
<boxed-text id="bx1"><label>Box 1</label><p>A box of its own.</p></boxed-text>
<graphic id="g1"/>
</floats-group>
<sub-article id="s1"><body><p>A reply.</p></body><floats-group><fig id="sf1"><label>Figure 1</label><caption><p>The reply's.</p></caption></fig></floats-group></sub-article>
</article>"""


def test_placement_takes_the_body_first_and_a_block_only():
    root = _root(prepare_jats(EDGES.encode("utf-8")))
    after = lambda ident: _by_id(root, ident).getnext()  # noqa: E731
    assert [after("p1").get("id"), after("f1").get("id"), after("t2").get("id")] == ["f1", "t2", "bx1"]  # the body's citation, not the appendix's
    assert _by_id(root, "f3").getparent().get("id") == "box" and _by_id(root, "f3").getprevious().get("id") == "p3"  # a box holds blocks of its own
    assert after("pa").get("id") == "f2"  # the footnote placed nothing; the next citation that stands in a block did
    group = _by_id(root, "g1").getparent()
    assert group.tag == "floats-group" and group.getparent().tag == "article" and len(group) == 1  # what is no float keeps its group
    assert _by_id(root, "sf1").getparent().getparent().get("id") == "s1"  # a sub-article's floats are its own, and stay
    assert not [t for t in root.iter("title") if t.text == "(untitled section)"]


def test_a_step_that_fails_costs_no_paper(monkeypatch):
    def half_way(root):
        root.find("body").set("seen", "yes")
        raise RuntimeError("a float it could not set")

    monkeypatch.setattr(jats_prep, "place_floats", half_way)
    plain = b'<article><body><sec><title>Results</title><p>Nothing else to change.</p></sec></body></article>'
    assert _root(prepare_jats(plain)).find("body").get("seen") == "yes"  # what it did before it stopped is well-formed, and kept
    labelled = b'<article><body><sec><title>Results</title><p>x</p><fig id="f1"><label>1</label><caption><p>One.</p></caption></fig></sec></body></article>'
    assert _text(_by_id(_root(prepare_jats(labelled)), "f1").find("label")) == "Figure 1"  # and the next step still ran


ACS = """<?xml version="1.0" encoding="UTF-8"?>
<article><body><sec><title>Results</title>
<p id="a">As shown in <xref rid="fig1" ref-type="fig">Figure <xref rid="fig1" ref-type="fig"/>
</xref>, the gel swelled; Figure <xref rid="fig1" ref-type="fig"/> and
<xref rid="tbl1" ref-type="table">Table </xref>A agree, as <xref rid="sch1" ref-type="scheme">Scheme
</xref> sets out. The cells in <xref rid="fig2" ref-type="fig">Figure 2</xref> grew.</p>
<fig id="fig1" fig-type="figure"><label>1</label><caption><p>Swelling.</p></caption></fig>
<fig id="sch1" fig-type="scheme"><label>1</label><caption><p>Synthesis.</p></caption></fig>
<fig id="fig2"><label>Figure 2</label><caption><p>Cells.</p></caption></fig>
<table-wrap id="tbl1"><label>1</label><caption><p>Ratios.</p></caption><table><tr><td>1</td></tr></table></table-wrap>
</sec></body></article>"""


def test_labels_and_calls_are_written_as_printed():
    root = _root(prepare_jats(ACS.encode("utf-8")))
    assert [_text(_by_id(root, i).find("label")) for i in ("fig1", "sch1", "tbl1", "fig2")] == ["Figure 1", "Scheme 1", "Table 1", "Figure 2"]
    assert _text(root.find(".//p")) == ("As shown in Figure 1 , the gel swelled; Figure 1 and Table 1A agree, as Scheme 1 sets out. "
                                         "The cells in Figure 2 grew.")
    # the outer call of ACS's nested pair holds an xref, so only the inner, empty one is filled
    outer = root.find(".//p/xref")
    assert outer.text == "Figure " and outer[0].text == "1"


KINDS = """<article><front><article-meta><title-group><article-title>Gels</article-title></title-group>
<fig id="ga"><label>1</label><caption><p>The graphical abstract.</p></caption></fig></article-meta></front>
<body><sec><title>Results</title>
<p id="calls">See <xref rid="cht1" ref-type="chart">Chart </xref>, <xref rid="fig1 fig2" ref-type="fig">Figures </xref>, <xref rid="fig1" ref-type="fig">Fig. </xref>.</p>
<p id="printed">As printed: <xref rid="fig2" ref-type="fig">Figure</xref> 2, <xref rid="fig2" ref-type="fig">Fig.</xref><xref rid="fig2" ref-type="fig">2</xref> and <xref rid="fig2" ref-type="fig">Figures </xref> <xref rid="fig2" ref-type="fig">2</xref>.</p>
<fig id="fig1"><label>S1</label><caption><p>One.</p></caption></fig>
<fig id="fig2"><label>2.</label><caption><title>Swelling of the gel of Figure <xref rid="fig1" ref-type="fig"/></title><p>Two.</p></caption></fig>
<fig id="cht1"><label>1</label><caption><p>A chart by its id.</p></caption></fig>
<fig id="x3" fig-type="Chart"><label>3</label><caption><p>A chart by its type.</p></caption></fig>
<fig id="sch2"><label>2</label><caption><p>A scheme by its id.</p></caption></fig>
<fig id="x4" fig-type="scheme"><label>4</label><caption><p>A scheme by its type.</p></caption></fig>
</sec></body></article>"""


def test_every_kind_of_float_is_named_and_called_as_printed():
    root = _root(prepare_jats(KINDS.encode("utf-8")))
    labels = {i: _text(_by_id(root, i).find("label")) for i in ("fig1", "fig2", "cht1", "x3", "sch2", "x4", "ga")}
    assert labels == {"fig1": "Figure S1", "fig2": "Figure 2", "cht1": "Chart 1", "x3": "Chart 3", "sch2": "Scheme 2", "x4": "Scheme 4",
                      "ga": "1"}  # the graphical abstract in <front> is no float of the text, and keeps its label
    assert _text(_by_id(root, "calls")) == "See Chart 1, Figures S1, 2, Fig. S1."
    assert _text(_by_id(root, "printed")) == "As printed: Figure 2, Fig.2 and Figures 2."  # a number the file prints after the call is not printed twice
    assert _by_id(root, "fig2").findtext("caption/title") == "Swelling of the gel of Figure S1"  # filled before the title was folded to text


def test_a_figure_with_no_caption_is_given_an_empty_one():
    root = _root('<article><body><sec><p>x</p><fig id="f1"><label>Figure 1</label><graphic/></fig><fig id="f2"><graphic/></fig>'
                 '<table-wrap id="t1"><label>Table 1</label><graphic/></table-wrap></sec></body></article>')
    assert name_floats(root)
    assert [[c.tag for c in _by_id(root, i)] for i in ("f1", "f2", "t1")] == [["label", "caption", "graphic"], ["caption", "graphic"], ["label", "caption", "graphic"]]
    assert all(len(c) == 0 and not c.text for c in root.iter("caption"))  # Docling reads the label alone, not "Figure 1None"


def test_a_panel_letter_is_never_taken_for_a_numberless_call():
    raw = ('<article><body><sec><p>Collagen aligned (Fig. 2a, <xref rid="F2" ref-type="fig">d</xref>; '
           '<xref rid="F2" ref-type="fig">h–i</xref>).</p><fig id="F2"><label>Fig. 2</label><caption><p>Alignment.</p></caption></fig></sec></body></article>')
    root = _root(raw)
    before = etree.tostring(root)
    assert not name_floats(root) and etree.tostring(root) == before


def test_a_table_label_in_markup_becomes_its_text():
    root = _root('<article><body><sec><table-wrap id="t1"><label><bold>Table 1.</bold></label><caption><p>Current Advances.</p></caption>'
                 '<table><tr><td>1</td></tr></table></table-wrap></sec></body></article>')
    assert name_floats(root)
    label = root.find(".//label")
    assert label.text == "Table 1." and len(label) == 0  # Docling reads a table's label as .text: "None" before the caption, before


def test_an_image_of_a_table_is_read_as_a_figure():
    root = _root('<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><sec><table-wrap id="T1"><label>Table 1</label>'
                 '<caption><p>Comparative analysis.</p></caption><graphic xlink:href="ijd-61-116-g001"/></table-wrap></sec></body></article>')
    assert name_floats(root)
    fig = root.find(".//fig")
    assert root.find(".//table-wrap") is None and fig.get("id") == "T1"
    assert [c.tag for c in fig] == ["label", "caption", "graphic"] and _text(fig.find("label")) == "Table 1" and _text(fig.find("caption")) == "Comparative analysis."


def test_floats_in_counts_what_the_file_holds_where_docling_reads():
    assert floats_in(FLOATS.encode("utf-8")) == {"figures": 5, "tables": 2}  # f1, f2a, f2b, f3, f4; the graphical abstract is not counted
    assert floats_in((FIXTURES / "PMC11278924.xml").read_bytes()) == {"figures": 6, "tables": 1}
    image = b'<article><body><sec><table-wrap id="T1"><label>Table 1</label><graphic/></table-wrap><table-wrap id="T2"><alternatives><table/></alternatives></table-wrap></sec></body></article>'
    assert floats_in(image) == {"figures": 1, "tables": 1}  # an image of a table is what name_floats makes it: a figure
    assert floats_in(b'<article><body/><back><app><fig id="A1"/></app></back></article>') == {"figures": 1, "tables": 0}  # an appendix's too
    assert floats_in(b"not xml") is None


def test_a_reading_with_fewer_floats_than_its_file_says_so():
    two = b'<article><body><sec><title>Results</title><p>See both.</p><fig id="f1"><label>Figure 1</label></fig><fig id="f2"><label>Figure 2</label></fig></sec></body></article>'
    tree = build_tree(_jats([("section_header", "Results", {"level": 1}), ("text", "See both.")]), "k")
    note = floats_note(tree, two)
    assert note == {"kind": "xml-floats", "node_id": "k", "page": None, "message": "the XML holds 2 figures and 0 tables; this reading has 0 and 0"}
    micromachines = build_tree(json.loads((FIXTURES / "PMC11278924.jats.docling.json").read_text("utf-8")), "doi:10.3390/mi15070851")
    assert floats_note(micromachines, (FIXTURES / "PMC11278924.xml").read_bytes()) is None  # all six figures and the table read
    assert floats_note(micromachines, None) is None  # a PDF's reading has no file of floats to answer to
    charted = SimpleNamespace(root=SimpleNamespace(node_id="k"), walk=lambda: [SimpleNamespace(type="chart"), SimpleNamespace(type="picture")])
    assert floats_note(charted, two) is None  # a figure read as a chart is a figure read


def test_docling_reads_the_placed_floats(tmp_path, monkeypatch):
    """The bytes as the worker hands them to Docling's JATS backend, read into a tree: every float
    the file holds is a node, in the section that cites it."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    pytest.importorskip("docling")
    from docling.backend.xml.jats_backend import JatsDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import InputDocument

    from litrag_parser.worker import jats_stream

    def read(xml: str):
        path = tmp_path / "floats.xml"
        path.write_text(xml, "utf-8")
        raw = jats_stream(path).stream.read()
        backend = JatsDocumentBackend(InputDocument(path_or_stream=BytesIO(raw), format=InputFormat.XML_JATS, backend=JatsDocumentBackend, filename=path.name), BytesIO(raw))
        return build_tree(backend.convert().export_to_dict(), "k")

    tree = read(FLOATS)
    pictures = [n for n in tree.walk() if n.type == "picture"]
    tables = [n for n in tree.walk() if n.type == "table"]
    assert len(pictures) == 5 and len(tables) == 2  # one picture per figure of the text, the graphical abstract none
    sections = {n.node_id: n for n in tree.walk() if n.type == "section"}
    f1 = next(p for p in pictures if any(c.text.startswith("Figure 1 ") for c in p.children))
    assert [c.text for c in f1.children if c.type == "caption"] == ["Figure 1 Strength."]
    assert sections[f1.parent].heading == "Results" and sections[f1.parent].role == "results"
    t9 = next(t for t in tables if any("Uncited" in c.text for c in t.children))
    assert sections[t9.parent].heading == "(untitled section)" and sections[t9.parent].role == "other"
    assert floats_note(tree, FLOATS.encode("utf-8")) is None
    bare = read('<article><body><sec><title>Results</title><p>See <xref ref-type="fig" rid="f1">Figure 1</xref>.</p></sec></body><floats-group>'
                '<fig id="f1"><label>Figure 1</label><graphic/></fig><fig id="f2"><graphic/></fig><table-wrap id="t1"><label>Table 1</label><graphic/></table-wrap></floats-group></article>')
    captions = [[c.text for c in n.children if c.type == "caption"] for n in bare.walk() if n.type == "picture"]
    assert captions == [["Figure 1"], [], ["Table 1"]]  # no caption closes on "None"


def test_an_untitled_section_is_never_asked_what_it_names(fake_oracle, monkeypatch):
    named = []  # the headings canonical_of was asked about with the embedder at hand
    canonical_of = tree_module.canonical_of

    def spy(text, oracle=None):
        if oracle is not None:
            named.append(text)
        return canonical_of(text, oracle)

    monkeypatch.setattr(tree_module, "canonical_of", spy)
    tree = build_tree(_jats([("section_header", "Results", {"level": 1}), ("text", "The threads were strong."),
                             ("section_header", "(untitled section)", {"level": 2}), ("text", "A panel heading the layout dropped."),
                             ("section_header", "Quarterly gazebo notes", {"level": 1}), ("text", "A note."),
                             ("section_header", "(heading not detected)", {"level": 1}), ("text", "Words under no heading."),
                             ("section_header", "(untitled section)", {"level": 1}), ("text", "Table 9 Uncited.")]), "k")
    sections = [(n.heading, n.level, n.role, n.canonical) for n in tree.walk() if n.type == "section" and n.heading.startswith("(")]
    assert sections == [("(untitled section)", 2, "results", None),  # nested, its parent's lane, as an empty heading's
                        ("(heading not detected)", 1, "other", None), ("(untitled section)", 1, "other", None)]
    assert "Quarterly gazebo notes" in named and not [h for h in named if h.startswith("(")]
    assert fake_oracle.summary()["kinds"]["heading"]["asked"] == 1  # the heading the vocabulary does not know is asked; the reader's own are not
    assert fake_oracle.recall("heading", normalise("Quarterly gazebo notes")) is not None
    assert fake_oracle.recall("heading", normalise("(untitled section)")) is None
