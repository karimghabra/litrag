import json
from pathlib import Path

import pytest

from litrag_parser.tree import build_tree, infer_level, numbering_depth, rescue_merged_heading, undouble

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def nar():
    return build_tree(json.loads((FIXTURES / "PMC3258128.docling.json").read_text("utf-8")), "doi:10.1093/nar/gkr715")


@pytest.fixture(scope="module")
def mi():
    return build_tree(json.loads((FIXTURES / "PMC11278924.docling.json").read_text("utf-8")), "doi:10.3390/mi15070851")


@pytest.fixture(scope="module")
def mi_jats():
    return build_tree(json.loads((FIXTURES / "PMC11278924.jats.docling.json").read_text("utf-8")), "doi:10.3390/mi15070851")


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
    assert nar.roles["methods"] == 23 and nar.roles["results"] == 22  # one results paragraph was split across pages 4 and 5; it is one again
    front = sections(nar, 1)[0]
    # the lines above the abstract, each a typed node, not a paragraph apiece
    assert [(c.type, c.label, c.page) for c in front.children] == [("meta", "authors", 1), ("meta", "affiliations", 1), ("meta", "dates", 1)]
    assert front.children[0].text.startswith("Shuai Li 1") and "Xiaofei Zheng" in front.children[0].text


def test_numbering_gives_depth_and_a_merged_heading_is_rescued(mi):
    tops = [(n.heading, n.role) for n in sections(mi, 1)]
    assert ("3. Experimental Results", "results") in tops
    results = next(n for n in sections(mi, 1) if n.role == "results")
    subs = [c.heading for c in results.children if c.type == "section"]
    assert subs[0].startswith("3.1. Quantification of Crosslinking Degree")
    assert subs[1].startswith("3.2.") and subs[3].startswith("3.4. Simulation Results")
    # results paragraphs are results, not methods
    assert mi.roles["results"] > mi.roles["methods"] and mi.roles["methods"] < 40


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
    # the page's own type, where it could be read: it settles the depth either way
    assert infer_level("Tendon biology", 1, True, typed=True) == 1
    assert infer_level("Tendon biology", 1, True, typed=False) == 2
    assert infer_level("2.1 Cells", 1, True, typed=True) == 2  # numbering still wins
    assert infer_level("Statistical methods", 1, True) == 1  # a lane by meaning, with nothing else to go on
    assert infer_level("Statistical methods", 1, True, typed=False) == 2  # set as this paper's subsections are
    assert undouble("3.4. Simulation Results 3.4. Simulation Results") == "3.4. Simulation Results"
    assert undouble("Results") == "Results"
    r = rescue_merged_heading({"label": "list_item", "text": "Experimental Results 3.1. Quantification of Crosslinking 3.1. Quantification", "self_ref": "#/texts/1", "prov": []})
    assert [x["text"] for x in r] == ["3. Experimental Results", "3.1. Quantification of Crosslinking"]
    assert rescue_merged_heading({"label": "list_item", "text": "Some list item 3.1. not a heading", "self_ref": "#/texts/2"}) is None


def test_jats_inline_runs_are_one_paragraph(mi_jats):
    # Docling cuts "(<italic>w</italic>/<italic>v</italic>)" into runs in an inline group;
    # they are one paragraph, not five nodes named "w", "/", "v".
    paragraphs = [n for n in mi_jats.walk() if n.type == "paragraph"]
    assert all(len(n.text) > 2 for n in paragraphs)
    texts = "\n".join(n.text for n in paragraphs)
    assert "genipin solution (w/v) prepared in 90% ethanol" in texts
    assert "(n = 8/group)" in texts
    assert "was set at p < 0.05." in texts
    assert "0.35/µm3 in 2% (4 h) ELAC" in texts  # a superscript takes no space before it
    methods = [n for n in paragraphs if n.role == "methods"]
    assert 8 <= len(methods) <= 12, [n.text[:40] for n in methods]


def test_jats_formulas_survive_as_text(mi_jats):
    # MathML-only formulas: the display equation is a formula node between "the equation below:" and "where …",
    # and the inline symbols sit in the sentence (see mathml.py, which gives Docling a <tex-math> to read).
    nodes = list(mi_jats.walk())
    formulas = [n for n in nodes if n.type == "formula"]
    assert [n.text for n in formulas] == ["Crosslinking Degree=100×(1−A_x/W_x)/(A_{N_avg}/W_{N_avg})"]
    assert formulas[0].role == "methods" and formulas[0].ancestry[-1].startswith("2.4.")
    texts = [n.text for n in nodes if n.type == "paragraph"]
    assert any(t.startswith("where A_x represents the absorbance") and "W_{N_avg} represents" in t for t in texts)
    assert any(t.endswith("using the equation below:") for t in texts)


def test_decorative_pictures_are_dropped_and_counted():
    def pic(i, page, l, t, w, h, caption=None):
        p = {"self_ref": f"#/pictures/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": "picture", "captions": [], "prov": [{"page_no": page, "bbox": {"l": l, "t": t, "r": l + w, "b": t - h, "coord_origin": "BOTTOMLEFT"}}]}
        if caption is not None:
            p["captions"] = [{"$ref": caption}]
        return p

    pictures = [
        pic(0, 1, 40, 820, 43, 12),  # the journal's logo: tiny, one per page
        pic(1, 2, 40, 820, 43, 12),
        pic(2, 3, 40, 820, 43, 12),
        pic(3, 1, 400, 40, 120, 30),  # a stamp: larger, but at one spot on three pages
        pic(4, 2, 400, 40, 120, 30),
        pic(5, 3, 400, 40, 120, 30),
        pic(6, 1, 60, 600, 300, 200, caption="#/texts/1"),  # the real figure
    ]
    doc = {
        "name": "d",
        "body": {"self_ref": "#/body", "children": [{"$ref": "#/texts/0"}] + [{"$ref": p["self_ref"]} for p in pictures]},
        "texts": [
            {"self_ref": "#/texts/0", "parent": {"$ref": "#/body"}, "children": [], "label": "section_header", "level": 1, "text": "2. Methods", "prov": [{"page_no": 1, "bbox": {"l": 50, "t": 700, "r": 300, "b": 690}}]},
            {"self_ref": "#/texts/1", "parent": {"$ref": "#/pictures/6"}, "children": [], "label": "caption", "text": "Figure 1. A thread.", "prov": []},
        ],
        "pictures": pictures,
        "tables": [],
        "groups": [],
        "pages": {str(i): {"page_no": i, "size": {"width": 600, "height": 850}} for i in (1, 2, 3)},
    }
    tree = build_tree(doc, "k")
    kept = [n for n in tree.walk() if n.type == "picture"]
    assert len(kept) == 1 and kept[0].children[0].text == "Figure 1. A thread."
    assert tree.dropped == {"picture": 6}
    assert tree.to_dict()["dropped"] == {"picture": 6}


def _doc(texts, pages=(1, 2)):
    items = []
    for i, (label, text, page) in enumerate(texts):
        items.append({"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "level": 1 if label == "section_header" else None, "prov": [{"page_no": page, "bbox": {"l": 50, "t": 700 - 20 * i, "r": 500, "b": 690 - 20 * i, "coord_origin": "BOTTOMLEFT"}}]})
    return {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items, "pictures": [], "tables": [], "groups": [], "pages": {str(p): {"page_no": p, "size": {"width": 600, "height": 850}} for p in pages}}


def test_a_paragraph_split_at_a_page_break_is_joined():
    doc = _doc([
        ("section_header", "2 Not so fast?", 1),
        ("text", "This mirrors other proposals (Friston, 2019, Fields et al., 2022, Friston et al.,", 1),
        ("text", "2023, Fields, 2024)-to optimize trade-offs between heterogeneous goals.", 2),
        ("text", "Empirically plausible models of amoeboid chemotaxis", 2),
        ("text", "( K ≈ 2 ) (Sect. 5) and planarian head regeneration converge.", 2),
        ("text", "A new paragraph starts here.", 2),
        ("text", "Received: 30 June 2025 / Accepted: 8 October 2025", 2),
        ("text", "© The Author(s) 2025", 2),
    ])
    tree = build_tree(doc, "k")
    paragraphs = [n.text for n in tree.walk() if n.type == "paragraph"]
    assert paragraphs == [
        "This mirrors other proposals (Friston, 2019, Fields et al., 2022, Friston et al., 2023, Fields, 2024)-to optimize trade-offs between heterogeneous goals.",
        "Empirically plausible models of amoeboid chemotaxis ( K ≈ 2 ) (Sect. 5) and planarian head regeneration converge.",
        "A new paragraph starts here.",
        "Received: 30 June 2025 / Accepted: 8 October 2025",  # unfinished, but what follows starts a new sentence in capitals
        "© The Author(s) 2025",
    ]
    assert tree.repairs == {"joined": 2}
    # a break three pages away is not a continuation
    far = _doc([("section_header", "1 Intro", 1), ("text", "cut short and the", 1), ("text", "rest of it.", 4)], pages=(1, 4))
    assert [n.text for n in build_tree(far, "k").walk() if n.type == "paragraph"] == ["cut short and the", "rest of it."]


def test_recurring_page_furniture_is_dropped():
    doc = _doc([("section_header", "1 Intro", 1), ("text", "1 3", 1), ("text", "Real text.", 1), ("text", "1 3", 2), ("text", "More text.", 2), ("text", "1 3", 3), ("text", "Once only", 3)], pages=(1, 2, 3))
    tree = build_tree(doc, "k")
    assert [n.text for n in tree.walk() if n.type == "paragraph"] == ["Real text.", "More text.", "Once only"]
    assert tree.dropped == {"furniture": 3}


def test_a_paragraph_continues_across_a_figure_and_a_running_head():
    # page 3 ends mid-sentence; page 4 opens with a running head, a figure and its caption, then the rest of the sentence
    items = [
        {"self_ref": "#/texts/0", "parent": {"$ref": "#/body"}, "children": [], "label": "section_header", "level": 1, "text": "2. Methods", "prov": [{"page_no": 3, "bbox": {"l": 50, "t": 700, "r": 300, "b": 690}}]},
        {"self_ref": "#/texts/1", "parent": {"$ref": "#/body"}, "children": [], "label": "text", "text": "The cell migration assay was modified from Cornwell et al. A cell seeding density of 1 - 10 6", "prov": [{"page_no": 3, "bbox": {"l": 50, "t": 600, "r": 300, "b": 500}}]},
        {"self_ref": "#/texts/2", "parent": {"$ref": "#/body"}, "children": [], "label": "page_header", "text": "JOURNAL OF BIOMEDICAL MATERIALS RESEARCH A", "prov": [{"page_no": 4, "bbox": {"l": 50, "t": 830, "r": 300, "b": 820}}]},
        {"self_ref": "#/pictures/0", "parent": {"$ref": "#/body"}, "children": [], "label": "picture", "captions": [{"$ref": "#/texts/3"}], "prov": [{"page_no": 4, "bbox": {"l": 50, "t": 800, "r": 300, "b": 600}}]},
        {"self_ref": "#/texts/3", "parent": {"$ref": "#/pictures/0"}, "children": [], "label": "caption", "text": "Figure 2. The construct.", "prov": [{"page_no": 4, "bbox": {"l": 50, "t": 590, "r": 300, "b": 580}}]},
        {"self_ref": "#/texts/4", "parent": {"$ref": "#/body"}, "children": [], "label": "text", "text": "cells/mL-gel was used for both cell types. The gel was poured on the wider end.", "prov": [{"page_no": 4, "bbox": {"l": 50, "t": 560, "r": 300, "b": 500}}]},
    ]
    doc = {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": i["self_ref"]} for i in items if i["label"] != "caption"]}, "texts": [i for i in items if not i["self_ref"].startswith("#/pictures")], "pictures": [i for i in items if i["self_ref"].startswith("#/pictures")], "tables": [], "groups": [], "pages": {"3": {"page_no": 3, "size": {"width": 600, "height": 850}}, "4": {"page_no": 4, "size": {"width": 600, "height": 850}}}}
    tree = build_tree(doc, "k")
    paragraphs = [n.text for n in tree.walk() if n.type == "paragraph"]
    assert paragraphs == ["The cell migration assay was modified from Cornwell et al. A cell seeding density of 1 × 10^6 cells/mL-gel was used for both cell types. The gel was poured on the wider end."]
    assert tree.repairs == {"glyphs": 1, "joined": 1}
    joined = next(n for n in tree.walk() if n.type == "paragraph")
    assert joined.page == 3 and joined.pages == [3, 4]  # the node knows both pages it came from
    kinds = [n.type for n in tree.walk() if n.type in ("paragraph", "picture", "caption")]
    assert kinds == ["paragraph", "picture", "caption"]  # the figure keeps its place, after the paragraph it interrupted


def test_the_judge_is_asked_only_where_the_rules_are_silent():
    asked = []

    def judge(a, b, ctx):
        asked.append((a[-30:], b[:30], ctx["page"]))
        return b.startswith("Statistical")  # the model's verdict, faked: join the first pair, not the second

    doc = _doc([
        ("section_header", "2 Methods", 1),
        ("text", "The samples were fixed in 4% paraformaldehyde and stained with DAPI for the nuclei and phalloidin for the cytoskeleton", 1),
        ("text", "Statistical analysis of the aligned constructs was performed using one-way ANOVA with Tukey post hoc tests.", 2),
        ("text", "A second matter entirely, which the layout model put on the page after a figure and without a full stop", 2),
        ("text", "Results were expressed as mean and standard deviation for all groups of samples tested here.", 2),
        ("text", "Received: 30 June 2025 / Accepted: 8 October 2025", 2),
        ("text", "cells were counted.", 2),
    ], pages=(1, 2))
    tree = build_tree(doc, "k", judge=judge)
    paragraphs = [n.text for n in tree.walk() if n.type == "paragraph"]
    assert paragraphs[0].startswith("The samples were fixed") and paragraphs[0].endswith("Tukey post hoc tests.")  # the judge joined it
    assert paragraphs[1].startswith("A second matter") and paragraphs[2].startswith("Results were")  # the judge kept these apart
    assert [a[2] for a in asked] == [2, 2]  # asked twice; the lowercase "cells were counted." was the rules' own join
    assert tree.repairs == {"judged": 1, "joined": 1}
    # no judge: the rules alone, the pair stays split
    assert len([n for n in build_tree(doc, "k").walk() if n.type == "paragraph"]) == 5


def test_what_the_judge_is_never_asked_about():
    from litrag_parser.tree import _judge_candidate

    prose = "the cells were resuspended in serum-supplemented medium and seeded at a density that matched the earlier experiments"
    nxt = "The primary culture was passaged after fourteen days and expanded in the same medium for three passages."
    assert _judge_candidate(prose, nxt)  # mid-sentence, then a capital: the judge's case
    assert not _judge_candidate("These approaches aim to recover the original tendon tissue architecture and function.[[8]]", nxt)  # a citation after the full stop
    assert not _judge_candidate("It has been used widely in Asian medicine for its lower toxicity and anti-inflammatory properties. 29,30", nxt)
    assert not _judge_candidate("Immobilization of rhBMP-2 to succinylated type I atelocollagen", nxt)  # a heading the layout model called text
    assert not _judge_candidate("See the Terms and Conditions on Wiley Online Library for rules of use; OA articles are governed by the applicable Creative Commons License", nxt)
    assert not _judge_candidate(prose, "Received: 30 June 2025 / Accepted: 8 October 2025 / Published online: 1 November 2025 by the journal")
    assert not _judge_candidate(prose, "cells were counted.")  # lowercase: the rules' own case, never the judge's


def test_a_paragraph_docling_carried_over_a_page_break_knows_both_pages():
    doc = _doc([("section_header", "2 Methods", 2), ("text", "A total of 25 animals were used in this study; the incision went through the overlying skin and underlying muscle layers.", 2)], pages=(2, 3))
    doc["texts"][1]["prov"].append({"page_no": 3, "bbox": {"l": 50, "t": 800, "r": 500, "b": 700, "coord_origin": "BOTTOMLEFT"}})
    node = next(n for n in build_tree(doc, "k").walk() if n.type == "paragraph")
    assert node.page == 2 and node.pages == [2, 3]


def test_a_table_footnote_is_a_node_after_its_table():
    table = {"self_ref": "#/tables/0", "parent": {"$ref": "#/body"}, "children": [{"$ref": "#/texts/1"}, {"$ref": "#/texts/2"}], "label": "table", "captions": [{"$ref": "#/texts/1"}], "data": {"grid": [[{"text": "Group"}, {"text": "Score"}], [{"text": "ECC"}, {"text": "1.2"}]]}, "prov": [{"page_no": 4, "bbox": {"l": 50, "t": 700, "r": 540, "b": 630}}]}
    texts = [
        {"self_ref": "#/texts/0", "parent": {"$ref": "#/body"}, "children": [], "label": "section_header", "level": 1, "text": "3. Results", "prov": [{"page_no": 4, "bbox": {"l": 50, "t": 760, "r": 300, "b": 750}}]},
        {"self_ref": "#/texts/1", "parent": {"$ref": "#/tables/0"}, "children": [], "label": "caption", "text": "TABLE I. Host response scores.", "prov": [{"page_no": 4, "bbox": {"l": 50, "t": 710, "r": 540, "b": 702}}]},
        {"self_ref": "#/texts/2", "parent": {"$ref": "#/tables/0"}, "children": [], "label": "footnote", "text": "a Parameters were defined as follows: granulation tissue, a precursor to fibrous tissue formation.", "prov": [{"page_no": 4, "bbox": {"l": 54, "t": 627, "r": 540, "b": 592}}]},
        {"self_ref": "#/texts/3", "parent": {"$ref": "#/body"}, "children": [], "label": "text", "text": "Scores rose with time in every group.", "prov": [{"page_no": 4, "bbox": {"l": 50, "t": 580, "r": 300, "b": 560}}]},
    ]
    doc = {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": "#/texts/0"}, {"$ref": "#/tables/0"}, {"$ref": "#/texts/3"}]}, "texts": texts, "tables": [table], "pictures": [], "groups": [], "pages": {"4": {"page_no": 4, "size": {"width": 600, "height": 850}}}}
    tree = build_tree(doc, "k")
    kinds = [(n.type, n.text[:22]) for n in tree.walk() if n.type in ("table", "caption", "footnote", "paragraph")]
    assert kinds == [("table", ""), ("caption", "TABLE I. Host response"), ("footnote", "a Parameters were defi"), ("paragraph", "Scores rose with time ")]



# ---- a heading's depth from the type it is set in ----


def typed_doc():
    """A review whose layout model gave every header one level: two headings set as the
    paper's own sections are, one set as its subsections are."""
    def head(i, text, page, cap, font, level=1):
        return {"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": "section_header", "text": text, "level": level,
                "_cap": cap, "_font": font, "prov": [{"page_no": page, "bbox": {"l": 54, "t": 700, "r": 200, "b": 690, "coord_origin": "BOTTOMLEFT"}}]}

    def para(i, text, page):
        return {"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": "text", "text": text,
                "_cap": 6.1, "_font": "Gill-Regular/400", "prov": [{"page_no": page, "bbox": {"l": 54, "t": 660, "r": 290, "b": 600, "coord_origin": "BOTTOMLEFT"}}]}

    items = [
        {"self_ref": "#/texts/0", "parent": {"$ref": "#/body"}, "children": [], "label": "title", "text": "Genipin crosslinked biomaterials in the skeletal system", "prov": [{"page_no": 1, "bbox": {"l": 54, "t": 760, "r": 400, "b": 740, "coord_origin": "BOTTOMLEFT"}}]},
        head(1, "Introduction", 1, 8.2, "Gill-Bold/700"),
        para(2, "Tendon injuries are common and heal slowly, and the repaired tissue rarely regains the strength of the tissue it replaces.", 1),
        head(3, "Use of genipin in tissue regeneration", 2, 8.2, "Gill-Bold/700"),
        para(4, "Genipin is a natural crosslinker extracted from the fruit of Gardenia jasminoides and used to stabilise collagen scaffolds.", 2),
        head(5, "Articular cartilage regeneration", 2, 8.2, "Gill-MediumItalic/500"),
        para(6, "Crosslinked scaffolds seeded with chondrocytes kept their shape for twelve weeks in the defect and carried load.", 2),
        head(7, "Conclusion", 3, 8.2, "Gill-Bold/700"),
        para(8, "Genipin crosslinking is a promising route to stable scaffolds, and its safety in man is still to be shown.", 3),
    ]
    return {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": it["self_ref"]} for it in items]}, "texts": items,
            "pictures": [], "tables": [], "groups": [], "pages": {str(p): {"page_no": p, "size": {"width": 600, "height": 800}} for p in (1, 2, 3)}}


def test_a_heading_set_as_the_papers_own_sections_are_opens_a_section():
    tree = build_tree(typed_doc(), "k")
    tops = [(n.heading, n.role, n.level) for n in sections(tree) if n.level == 1]
    assert ("Use of genipin in tissue regeneration", "other", 1) in tops  # not a child of the introduction
    topical = next(n for n in sections(tree) if n.heading == "Use of genipin in tissue regeneration")
    assert topical.children[0].role == "other"  # and its prose is not the introduction's
    # the subheading, set in the italic the paper uses for its subsections, stays inside it
    sub = next(n for n in sections(tree) if n.heading == "Articular cartilage regeneration")
    assert sub.level == 2 and sub.parent == topical.node_id


def test_the_type_rule_writes_down_what_it_moved_and_why():
    tree = build_tree(typed_doc(), "k")
    moved = [e for e in tree.repairs.log if e["kind"] == "heading_level"]
    assert [e["after"] for e in moved] == ["Use of genipin in tissue regeneration — a section of its own"]
    assert moved[0]["before"] == "Use of genipin in tissue regeneration — a subsection of 'Introduction'"
    assert moved[0]["page"] == 2 and moved[0]["box"] is not None
    assert "Gill-Bold/700" in moved[0]["why"] and tree.repairs["heading_level"] == 1


def test_a_figures_label_in_the_sections_type_stays_a_label():
    doc = typed_doc()
    for it in doc["texts"]:
        if it["text"] == "Use of genipin in tissue regeneration":
            it["text"] = "Figure 2"  # journals set figure labels in the same bold as their headings
    tree = build_tree(doc, "k")
    assert not [e for e in tree.repairs.log if e["kind"] == "heading_level"]
    assert next(n for n in sections(tree) if n.heading == "Figure 2").level == 2


def test_nothing_is_moved_where_the_headings_are_set_smaller_than_the_body():
    doc = typed_doc()
    doc["_cap"] = 9.0  # the body's capitals stand taller than the headings': the measurement is not to be trusted
    tree = build_tree(doc, "k")
    assert not [e for e in tree.repairs.log if e["kind"] == "heading_level"]


def test_nothing_is_moved_where_the_page_shows_only_one_section_of_its_kind():
    doc = typed_doc()
    for it in doc["texts"]:
        if it["text"] == "Conclusion":
            it["text"] = "Closing thoughts"  # now only "Introduction" names a lane: one anchor, no type to match
    tree = build_tree(doc, "k")
    assert not [e for e in tree.repairs.log if e["kind"] == "heading_level"]
    assert next(n for n in sections(tree) if n.heading == "Use of genipin in tissue regeneration").level == 2
