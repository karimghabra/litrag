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
    assert [(c.type, c.label, c.page) for c in front.children] == [("meta", "authors", 1), ("meta", "affiliations", 1), ("meta", "dates", 1), ("meta", "dates", 1), ("meta", "notice", 1)]
    assert front.children[3].text.startswith("The Author(s) 2011. Published by")  # the imprint, read between the body's paragraphs
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
    ]
    # the publisher's own line, whole and on the first pages, is front matter wherever it was read:
    # the dates, and the imprint, which opens a line and never a sentence of a paper
    assert [(c.label, c.text) for c in tree.walk() if c.type == "meta"] == [
        ("dates", "Received: 30 June 2025 / Accepted: 8 October 2025"),
        ("notice", "© The Author(s) 2025"),
    ]
    assert tree.repairs == {"joined": 2}
    # a break three pages away is not a continuation
    far = _doc([("section_header", "1 Intro", 1), ("text", "cut short and the", 1), ("text", "rest of it.", 4)], pages=(1, 4))
    assert [n.text for n in build_tree(far, "k").walk() if n.type == "paragraph"] == ["cut short and the", "rest of it."]


def test_recurring_page_furniture_is_dropped():
    doc = _doc([("section_header", "1 Intro", 1), ("text", "1 3", 1), ("text", "Real text.", 1), ("text", "1 3", 2), ("text", "More text.", 2), ("text", "1 3", 3), ("text", "Once only", 3)], pages=(1, 2, 3))
    for it in doc["texts"]:
        if it["text"] == "1 3":
            it["prov"][0]["bbox"].update({"t": 40, "b": 30})  # a publisher's mark sits at the foot of every page
    tree = build_tree(doc, "k")
    assert [n.text for n in tree.walk() if n.type == "paragraph"] == ["Real text.", "More text.", "Once only"]
    assert tree.dropped == {"furniture": 3}


CONCLUDING = "Taken together these results show that the crosslinked scaffolds support cell growth while providing mechanical properties in the range of native cartilage, and that the method is simple enough for a clinical workflow."
FINDING = "The compressive modulus increased from 12 kPa to 48 kPa as the crosslinker concentration rose, and the swelling ratio decreased correspondingly over the seven days of the experiment."
ARGUING = "Our findings show that the crosslinked scaffolds support cell growth at a stiffness in the range of native cartilage, and several limitations of the approach should be acknowledged before any clinical use."


BANNER_PROSE = "Perovskite solar cells have reached efficiencies above 25 per cent in the laboratory, and their stability under damp heat is now the question that decides whether they leave it [1,2]."


def test_the_journals_banner_is_no_heading_though_prose_follows_it():
    # RSC prints "Chemical Science" at the head of the page; the layout model calls it a heading and the
    # introduction files under it. The record's abbreviated journal names it, and the prose is the body's
    doc = _doc([
        ("section_header", "Chemical Science", 1), ("section_header", "REVIEW", 1),
        ("title", "Stability of perovskite solar cells under damp heat", 1),
        ("text", "Jane Doe, a John Roe b and Wei Zhang a", 1),
        ("section_header", "1. Introduction", 1), ("text", BANNER_PROSE, 1),
        ("section_header", "2. Experimental", 2), ("text", "Films were annealed at 120 °C for 30 min and measured by XRD.", 2),
    ])
    tree = build_tree(doc, "k", record={"journal": "Chem Sci"})
    assert tree.repairs.get("furniture_headings") == 2  # the banner and the article's type
    assert [n.heading for n in tree.root.children if n.type == "section"] == ["Front matter", "1. Introduction", "2. Experimental"]
    intro = next(n for n in tree.root.children if n.heading == "1. Introduction")
    assert [c.text[:10] for c in intro.children if c.type == "paragraph"] == ["Perovskite"]
    assert [n["kind"] for n in tree.notes] == ["heading-refused", "heading-refused"]


def test_an_affiliation_read_as_a_heading_keeps_no_prose():
    # RSC sets its affiliations after the introduction's first lines, so the refusal cannot wait for the
    # front matter to end: an affiliation line heads no section wherever the model read it
    doc = _doc([
        ("title", "Stability of perovskite solar cells under damp heat", 1),
        ("section_header", "1. Introduction", 1), ("text", BANNER_PROSE, 1),
        ("section_header", "b Institute of Physics, Government College University, Lahore 54000, Pakistan", 1),
        ("text", "Damp heat drives iodide out of the absorber and the cells lose a fifth of their output [3].", 1),
    ])
    tree = build_tree(doc, "k", record={"journal": "RSC Adv"})
    assert tree.repairs.get("furniture_headings") == 1
    intro = next(n for n in tree.root.children if n.heading == "1. Introduction")
    assert [c.text[:10] for c in intro.children if c.type == "paragraph"] == ["Perovskite", "Damp heat "]


def test_a_folio_is_no_heading_anywhere_in_the_paper():
    doc = _doc([
        ("title", "Stability of perovskite solar cells under damp heat", 1),
        ("section_header", "2. Methods", 4), ("text", "Films were annealed at 120 °C and measured by XRD after each cycle of damp heat.", 4),
        ("section_header", "7 of 9", 4), ("text", "Each cycle ran for 1,000 hours at 85 per cent humidity, and the cells were measured again.", 4),
    ], pages=(1, 2, 3, 4))
    tree = build_tree(doc, "k", record={"journal": "Med Phys"})
    assert tree.repairs.get("furniture_headings") == 1
    methods = next(n for n in tree.walk() if n.heading == "2. Methods")
    assert [c.text[:10] for c in methods.children if c.type == "paragraph"] == ["Films were", "Each cycle"]


def test_a_two_word_heading_the_vocabulary_knows_survives_a_banner_rule():
    doc = _doc([
        ("title", "Stability of perovskite solar cells under damp heat", 1),
        ("section_header", "Chemical Science", 1),
        ("section_header", "Materials and methods", 1), ("text", "Films were annealed at 120 °C for 30 min and measured by XRD.", 1),
        ("section_header", "Results", 2), ("text", "The films kept 94 per cent of their output after 1,000 hours.", 2),
    ])
    tree = build_tree(doc, "k", record={"journal": "Chem Sci"})
    assert [(n.heading, n.role) for n in tree.root.children if n.type == "section"] == [
        ("Front matter", "other"), ("Materials and methods", "methods"), ("Results", "results"),
    ]


def test_a_title_that_recurs_as_its_own_running_head_is_not_refused():
    # a short paper whose title is printed at the head of every page: four words or more, so it is the
    # title and not a banner, and it stays the title
    doc = _doc([
        ("section_header", "Stability of perovskite solar cells under damp heat", 1),
        ("text", "Jane Doe, John Roe", 1),
        ("section_header", "1. Introduction", 1), ("text", BANNER_PROSE, 1),
    ])
    tree = build_tree(doc, "k", record={"journal": "Chem Sci"})
    assert tree.title.startswith("Stability of perovskite")
    assert "furniture_headings" not in tree.repairs


def test_a_page_read_out_of_order_in_one_column_is_put_back():
    # MDPI sets its reference list at the foot of the page: the layout model reads the list before the
    # text above it in the same column, and the conclusion's last paragraphs file under "References"
    doc = _doc([
        ("section_header", "4. Conclusions", 1), ("text", FINDING, 1),
        ("section_header", "References", 2), ("list_item", "1. Smith JA, Lee CD. Collagen crosslinking. J Biomed Mater Res. 2019;107:812.", 2), ("list_item", "2. Brown EF, Wu T. Tendon repair. Acta Biomater. 2020;101:44.", 2),
        ("text", CONCLUDING, 2), ("text", "Funding: this work was supported by a grant.", 2),
    ], pages=(1, 2))
    boxes = {2: (50, 500, 200, 190), 3: (50, 500, 180, 170), 4: (50, 500, 160, 150), 5: (50, 500, 760, 700), 6: (50, 500, 690, 680)}
    for k, (l, r, t, b) in boxes.items():
        doc["texts"][k]["prov"][0]["bbox"].update({"l": l, "r": r, "t": t, "b": b})
    tree = build_tree(doc, "k")
    assert tree.repairs.get("reordered_page") == 3  # the heading and its two entries were read too early
    conclusions = next(n for n in tree.walk() if n.heading == "4. Conclusions")
    assert [c.text[:14] for c in conclusions.children if c.type == "paragraph"] == ["The compressiv", "Taken together", "Funding: this "]  # the page's order: the text above the list, then the statements under it
    refs = next(n for n in tree.walk() if n.heading == "References")
    assert [c.type for c in refs.children] == ["list_item", "list_item"]


def test_a_heading_at_the_top_of_the_other_column_stays_where_it_was_read():
    # two columns: the right column's first heading stands above the left column's last paragraph and
    # is read after it, which is the order the page means
    doc = _doc([
        ("section_header", "3. Discussion", 2), ("text", ARGUING, 2), ("text", CONCLUDING, 2),
        ("section_header", "Conflicts of interest", 2), ("text", "The authors declare none.", 2),
    ], pages=(1, 2))
    boxes = {0: (50, 290, 700, 690), 1: (50, 290, 680, 500), 2: (50, 290, 490, 300), 3: (310, 460, 760, 750), 4: (310, 550, 740, 700)}
    for k, (l, r, t, b) in boxes.items():
        doc["texts"][k]["prov"][0]["bbox"].update({"l": l, "r": r, "t": t, "b": b})
    tree = build_tree(doc, "k")
    assert "reordered_page" not in tree.repairs
    assert [(n.heading, n.role) for n in tree.root.children if n.type == "section"] == [("3. Discussion", "discussion"), ("Conflicts of interest", "back")]


def test_the_same_words_at_three_different_heights_are_the_papers_own():
    # Diabetes Care prints "RESULTS" in its visual abstract, again in its structured abstract, and
    # over the section itself: three pages, three heights, and the section's heading is no running head
    doc = _doc([
        ("section_header", "RESULTS", 1), ("text", "A total of 43 participants were randomised in the trial.", 1),
        ("section_header", "RESULTS", 2), ("text", "In the first week, thirty-nine of the participants used the device.", 2),
        ("section_header", "RESULTS", 3), ("text", "The mean time in range was higher in the closed-loop group.", 3),
    ], pages=(1, 2, 3))
    tree = build_tree(doc, "k")
    assert [n.heading for n in tree.walk() if n.type == "section" and n.heading == "RESULTS"]  # the heading stands
    assert [len((n.text or "").split()) for n in tree.walk() if n.type in ("paragraph", "meta")] == [10, 11, 11]  # and its words with it
    assert "furniture" not in tree.dropped


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


def test_an_abbreviation_list_on_the_first_page_is_no_back_matter():
    # Scientific Reports sets its abbreviations beside the abstract and prints no "Introduction":
    # the prose after the list is the introduction, not eight paragraphs of back matter
    doc = _doc([
        ("section_header", "Abstract", 1),
        ("text", "Occupational hearing loss is a common work-related health problem among industrial workers in many settings.", 1),
        ("section_header", "Abbreviations", 1),
        ("text", "Threshold limit value", 1),
        ("text", "Personal protective equipment", 1),
        ("text", "Structural equation modeling", 1),
        ("text", "Hearing disorders are among the most common sensory impairments worldwide, and noise is the cause most often named in industry [1,2]. The burden falls hardest on workers whose exposure is long and whose protection is least, as several surveys of the last decade have shown. Prevalence rises with the years a worker has spent on the line.", 2),
        ("section_header", "Method", 2),
        ("text", "This analytical descriptive cross-sectional study was conducted in industrial workplaces.", 2),
    ], pages=(1, 2))
    tree = build_tree(doc, "k")
    intro = next(n for n in tree.walk() if n.type == "section" and n.role == "introduction")
    assert intro.label == "built" and len(intro.children) == 1
    assert tree.repairs.get("back_box_prose") == 1
    # the list itself is still its own section, and still back matter
    back = next(n for n in tree.walk() if n.type == "section" and n.heading == "Abbreviations")
    assert back.role == "back" and len(back.children) == 3


def test_a_real_abbreviations_section_after_the_body_keeps_its_prose():
    # the same heading once results are open: the prose under it stays back matter
    doc = _doc([
        ("section_header", "Results", 1),
        ("text", "Scores rose with time in every group of the cohort we followed.", 1),
        ("section_header", "Abbreviations", 2),
        ("text", "Threshold limit value", 2),
        ("text", "The authors thank the reviewers of an earlier draft for the objections raised there, which shaped the analysis reported above and are answered in full in the supplement (Smith et al., 2021).", 2),
    ], pages=(1, 2))
    tree = build_tree(doc, "k")
    assert not any(n.type == "section" and n.role == "introduction" for n in tree.walk())
    back = next(n for n in tree.walk() if n.type == "section" and n.heading == "Abbreviations")
    assert [c.role for c in back.children] == ["back", "back"]


def test_the_licence_sentence_is_front_matter_though_it_runs_long():
    # the shapes that carry their own length: a licence sentence of forty words is the publisher's,
    # and a paper whose own subject is licensing cites, which is what keeps its prose out of this
    doc = _doc([
        ("section_header", "1. Introduction", 1),
        ("text", "This article is distributed under the terms of the Creative Commons Attribution 4.0 International License, which permits any non-commercial use, sharing, adaptation, distribution and reproduction in any medium, provided the original author and source are credited and a link to the licence is given.", 1),
        ("text", "Academic Editors: Steven C. Cook and Simona Sagona", 1),
        ("text", "Published by Oxford University Press.", 1),
        ("text", "Open licensing of trial data is now required by most funders, and the terms of the Creative Commons family are the ones most often named (Smith et al., 2021). We read every policy published since 2019.", 1),
    ])
    tree = build_tree(doc, "k")
    meta = [c.text[:24] for c in tree.walk() if c.type == "meta"]
    assert meta == ["This article is distribu", "Academic Editors: Steven", "Published by Oxford Univ"]
    kept = [n.text[:34] for n in tree.walk() if n.type == "paragraph"]
    assert kept == ["Open licensing of trial data is no"]


def test_a_subsection_goes_back_to_the_numbered_section_already_read():
    # a two-column page read right column first puts "2 METHODS" and "2.1" before "1 INTRODUCTION",
    # so "2.2" arrives with the introduction open. The author numbered both: it belongs to methods
    doc = _doc([
        ("section_header", "2 METHODS", 2),
        ("section_header", "2.1 Patient data", 2),
        ("text", "Eighty patients were enrolled in the trial, of which fifty-four were treated at a single institution.", 2),
        ("section_header", "1 INTRODUCTION", 2),
        ("text", "In the radiation treatment of prostate cancer, hypofractionation has become the standard of care.", 2),
        ("section_header", "2.2 Setup error evaluation", 3),
        ("text", "To evaluate the setup error, we reviewed the cone-beam images of every fraction retrospectively.", 3),
    ], pages=(2, 3))
    tree = build_tree(doc, "k")
    assert not any("heading not detected" in (n.heading or "") for n in tree.walk())
    assert tree.repairs.get("renumbered_parent") == 1
    got = [(n.heading, n.role, n.depth) for n in tree.walk() if n.type == "section" and n.heading != "Front matter"]
    assert got == [("2 METHODS", "methods", 1), ("2.1 Patient data", "methods", 2),
                   ("2.2 Setup error evaluation", "methods", 2), ("1 INTRODUCTION", "introduction", 1)]


def test_a_subsection_whose_parent_was_never_read_still_stands_one_in():
    # nothing numbered "3" was read anywhere: an untitled section, role `other`, as before
    doc = _doc([
        ("section_header", "2 METHODS", 1),
        ("text", "Eighty patients were enrolled in the trial, of which fifty-four were treated here.", 1),
        ("section_header", "3.2 Residual errors", 2),
        ("text", "Median residual errors were below two millimetres in every direction we measured.", 2),
    ], pages=(1, 2))
    tree = build_tree(doc, "k")
    ghost = next(n for n in tree.walk() if "heading not detected" in (n.heading or ""))
    assert ghost.role == "other" and ghost.heading.startswith("3.")
    assert tree.repairs.get("renumbered_parent") is None


# ---- asserting only where the heading is canonical ------------------------------------------


def test_the_canonical_floor_reads_a_number_or_a_word(monkeypatch):
    """`on` means the measured pick — two of the three routes — and a number overrides it. The
    transfer gate chose two: requiring all three is more precise on DEV (0.9886 against 0.9788)
    and transfers slightly worse (-0.0043 against -0.0019) at less coverage."""
    import importlib

    import litrag_parser.tree as t

    assert t.CANONICAL_ONLY == 0  # off by default
    for raw, want in (("on", 2), ("3", 3), ("1", 1), ("off", 0), ("", 0)):
        monkeypatch.setenv("LITRAG_CANONICAL_ONLY", raw)
        importlib.reload(t)
        assert t.CANONICAL_ONLY == want, raw
    monkeypatch.delenv("LITRAG_CANONICAL_ONLY", raising=False)
    importlib.reload(t)
    assert t.CANONICAL_ONLY == 0


def test_a_heading_the_routes_do_not_know_keeps_its_lane_as_a_guess(monkeypatch):
    """The lane is withheld, not forgotten: `other` is what costs coverage, and `guess` is what
    makes the cost recoverable — by a review queue, by an escalation, or by a person."""
    import importlib
    import json as _json

    import litrag_parser.tree as t

    monkeypatch.setenv("LITRAG_CANONICAL_ONLY", "3")
    importlib.reload(t)
    try:
        doc = _json.loads((FIXTURES / "PMC11278924.docling.json").read_text("utf-8"))
        tree = t.build_tree(doc, "k")
        withheld = [n for n in tree.walk() if n.guess]
        assert withheld, "some heading should fail a floor of three with no embedder configured"
        for n in withheld:
            assert n.role == "other"
            assert n.guess != "other"
            assert 0.0 <= n.confidence <= 1.0
            assert set(n.reasons) == {"recognised_by", "withheld"}
        assert tree.repairs.get("lane_held_for_agreement") == len(withheld)
    finally:
        monkeypatch.delenv("LITRAG_CANONICAL_ONLY", raising=False)
        importlib.reload(t)


def test_the_floor_is_off_and_changes_nothing(monkeypatch):
    import json as _json

    from litrag_parser.tree import build_tree
    doc = _json.loads((FIXTURES / "PMC11278924.docling.json").read_text("utf-8"))
    tree = build_tree(doc, "k")
    assert not any(n.guess for n in tree.walk())
    assert "lane_held_for_agreement" not in tree.repairs
