import json
from pathlib import Path

import pytest

from litrag_parser.tree import build_tree, infer_level, numbering_depth, rescue_merged_heading, undouble

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def nar():
    return build_tree(json.loads((FIXTURES / "PMC3258128.docling.json").read_text()), "doi:10.1093/nar/gkr715")


@pytest.fixture(scope="module")
def mi():
    return build_tree(json.loads((FIXTURES / "PMC11278924.docling.json").read_text()), "doi:10.3390/mi15070851")


@pytest.fixture(scope="module")
def mi_jats():
    return build_tree(json.loads((FIXTURES / "PMC11278924.jats.docling.json").read_text()), "doi:10.3390/mi15070851")


def sections(tree, level=None):
    return [n for n in tree.walk() if n.type == "section" and (level is None or n.level == level)]


def test_title_is_taken_from_the_first_header_when_docling_labels_none(nar, mi):
    assert nar.title.startswith("Hepato-specific microRNA-122")
    assert mi.title.startswith("Computational and Experimental Characterization")
    # and it is not also a section
    assert not any(n.heading and n.heading.startswith("Hepato-specific") for n in sections(nar))


def test_flat_headers_become_a_hierarchy_and_inherit_the_lane(nar):
    methods = next(n for n in sections(nar, 1) if n.role == "methods")
    subs = [c for c in methods.children if c.type == "section"]
    assert [c.heading for c in subs][:3] == ["Cell lines and cultures", "Affinity purification experiments", "Real-time qRT-PCR for mRNA"]
    assert all(c.role == "methods" and c.level == 2 for c in subs)
    paragraph = subs[0].children[0]
    assert paragraph.type == "paragraph" and paragraph.role == "methods"
    assert paragraph.ancestry == ["MATERIALS AND METHODS", "Cell lines and cultures"]


def test_top_level_skeleton(nar):
    assert [(n.heading, n.role) for n in sections(nar, 1)] == [
        ("Front matter", "other"),
        ("ABSTRACT", "abstract"),
        ("INTRODUCTION", "introduction"),
        ("MATERIALS AND METHODS", "methods"),
        ("RESULTS", "results"),
        ("DISCUSSION", "discussion"),
        ("SUPPLEMENTARY DATA", "back"),
        ("FUNDING", "back"),
        ("REFERENCES", "references"),
    ]
    assert nar.has_methods
    assert nar.roles["methods"] == 23 and nar.roles["results"] == 23
    front = sections(nar, 1)[0]
    assert all(c.type == "paragraph" and c.page == 1 for c in front.children) and len(front.children) == 3


def test_numbering_gives_depth_and_a_merged_heading_is_rescued(mi):
    tops = [(n.heading, n.role) for n in sections(mi, 1)]
    assert ("3. Experimental Results", "results") in tops
    results = next(n for n in sections(mi, 1) if n.role == "results")
    subs = [c.heading for c in results.children if c.type == "section"]
    assert subs[0].startswith("3.1. Quantification of Crosslinking Degree")
    assert subs[1].startswith("3.2.") and subs[3].startswith("3.4. Simulation Results")
    # results paragraphs are results, not methods
    assert mi.roles["results"] > 50 and mi.roles["methods"] < 40


def test_a_heading_echoed_across_a_page_break_continues_its_section(mi):
    discussion = [n for n in sections(mi, 1) if n.heading == "4. Discussion"]
    assert len(discussion) == 1
    assert len(discussion[0].children) == 8


def test_tables_are_nodes_with_cells_and_captions(mi):
    tables = [n for n in mi.walk() if n.type == "table"]
    assert len(tables) == 1
    t = tables[0]
    assert t.table["rows"] == 5 and t.table["cols"] == 3
    assert t.role == "results" and t.page == 9
    assert t.children and t.children[0].type == "caption" and "Table" in t.children[0].text


def test_provenance_is_top_left_points_within_the_page(nar):
    p = next(n for n in nar.walk() if n.type == "paragraph" and n.page == 2)
    page = next(pg for pg in nar.pages if pg.page_no == 2)
    l, t, r, b = p.bbox
    assert 0 <= l < r <= page.width and 0 <= t < b <= page.height
    assert t < page.height / 2  # the first paragraph on the page is near its top, not its bottom


def test_jats_and_pdf_agree_on_the_skeleton(mi, mi_jats):
    pdf_tops = [n.role for n in sections(mi, 1) if n.role not in ("other", "back")]
    jats_tops = [n.role for n in sections(mi_jats, 1) if n.role not in ("other", "back")]
    assert [r for r in pdf_tops if r != "abstract"] == [r for r in jats_tops if r != "abstract"]
    assert mi_jats.has_methods
    # XML carries no page geometry: provenance is absent, not invented
    assert all(n.bbox is None for n in mi_jats.walk())


def test_helpers():
    assert numbering_depth("2.1.3 Cell culture") == 3 and numbering_depth("Results") is None
    assert infer_level("2.1 Cells", 1, True) == 2
    assert infer_level("Western blot", 1, True) == 2
    assert infer_level("RESULTS", 1, True) == 1
    assert infer_level("Western blot", 1, False) == 1
    assert undouble("3.4. Simulation Results 3.4. Simulation Results") == "3.4. Simulation Results"
    assert undouble("Results") == "Results"
    r = rescue_merged_heading({"label": "list_item", "text": "Experimental Results 3.1. Quantification of Crosslinking 3.1. Quantification", "self_ref": "#/texts/1", "prov": []})
    assert [x["text"] for x in r] == ["3. Experimental Results", "3.1. Quantification of Crosslinking"]
    assert rescue_merged_heading({"label": "list_item", "text": "Some list item 3.1. not a heading", "self_ref": "#/texts/2"}) is None
