"""The page's type as a witness for headings: rows made by hand with their runs of style, no
PDF needed — the body style, a run-in heading cut from its paragraph, a heading cut out of
the paragraph it was fused into, a heading's letters put back, a dropped heading recovered,
and a series of bold names left alone."""

from litrag_parser.tree import build_tree
from litrag_parser.typography import Row, apart, body_style, heading_kind, restyle, run_in_of

BODY = ("Minion-Regular", 390, 9.0)
BOLD = ("Minion-Bold", 700, 9.0)
BLACK = ("Minion-Black", 768, 11.0)
ITALIC = ("Minion-Italic", 390, 9.0)


def row(text, t, spans, l=54, r=None, h=9.0):
    # a row's width from its text, four points a character, the column 290 wide
    r = r if r is not None else min(290, l + 4.0 * len(text))
    return Row(text, l, t - h, r, t, [(s, x.replace(" ", "")) for s, x in spans])


def item(i, label, text, page, l, b, r, t):
    return {"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "prov": [{"page_no": page, "bbox": {"l": l, "t": t, "r": r, "b": b, "coord_origin": "BOTTOMLEFT"}}]}


def doc_of(texts, pictures=()):
    body = [{"$ref": t["self_ref"]} for t in texts] + [{"$ref": p["self_ref"]} for p in pictures]
    return {"name": "d", "body": {"self_ref": "#/body", "children": body}, "texts": list(texts), "pictures": list(pictures), "tables": [], "groups": [], "pages": {"2": {"page_no": 2, "size": {"width": 600, "height": 800}}}}


def body_rows(n=8, t=700):
    return [row("the collagen fibrils were aligned along the axis of the scaffold and", t - 12 * k, [(BODY, "the collagen fibrils were aligned along the axis of the scaffold and")]) for k in range(n)]


def order(doc):
    by = {t["self_ref"]: t for t in doc["texts"]}
    return [(by[c["$ref"]]["label"], by[c["$ref"]]["text"]) for c in doc["body"]["children"]]


def test_the_body_style_is_the_face_that_holds_the_text_at_its_own_size_and_a_style_stands_apart_or_not():
    small = ("Minion-Regular", 390, 7.5)
    rows = {1: body_rows(6), 2: [row("Smith J, Lee K. A paper. J Biomed Mater Res. 2019;107:812-821.", 700 - 10 * k, [(small, "Smith J, Lee K. A paper. J Biomed Mater Res. 2019;107:812-821.")]) for k in range(12)]}
    assert body_style(rows) == BODY  # a long reference list in a smaller size is not the body
    assert apart(BOLD, BODY) == "bold" and apart(BLACK, BODY) == "larger" and apart(ITALIC, BODY) == "italic" and apart(BODY, BODY) is None
    assert apart(("Arial-BoldMT", 400, 9.0), ("ArialMT", 400, 9.0)) == "bold"  # a bold face by its name where the weight is not written
    assert heading_kind(row("2. Current Therapeutic Strategies", 600, [(BLACK, "2. Current Therapeutic Strategies")]), BODY) == "larger"
    assert heading_kind(row("2.2.1. PU Coating onto the Bare Polyester Fabric", 600, [(BODY, "2.2.1. PU Coating onto the Bare Polyester Fabric")]), BODY) == "numbered"
    assert heading_kind(row("Figure 2. The scaffolds after 7 days.", 600, [(BOLD, "Figure 2. The scaffolds after 7 days.")]), BODY) is None
    assert heading_kind(row("Smith J, Lee K. A paper. J Biomed Mater Res. 2019;107:812-821.", 600, [(BOLD, "Smith J, Lee K. A paper. J Biomed Mater Res. 2019;107:812-821.")]), BODY) is None
    assert run_in_of(row("2.1. Non Surgical Approach. For small tears or overuse injuries,", 600, [(ITALIC, "2.1. Non Surgical Approach."), (BODY, "For small tears or overuse injuries,")]), BODY) == ("2.1. Non Surgical Approach", "italic")
    assert run_in_of(row("CFD Simulation: CFD simulations of the flow fields", 600, [(ITALIC, "CFD Simulation"), (BODY, ": CFD simulations of the flow fields")]), BODY) == ("CFD Simulation", "italic")
    assert run_in_of(row("Silk is another heavily studied natural polymer", 600, [(BOLD, "Silk"), (BODY, "is another heavily studied natural polymer")]), BODY) is None  # not closed by punctuation: emphasis


def test_a_run_in_heading_is_cut_from_the_front_of_its_paragraph():
    para = "2.1. Non Surgical Approach. For small tears or overuse injuries, physicians opt for conservative therapy such as rest and physiotherapy."
    texts = [item(0, "section_header", "2. Current Therapeutic Strategies", 2, 54, 720, 200, 731), item(1, "text", para, 2, 54, 660, 290, 700)]
    doc = doc_of(texts)
    rows = {2: [row("2. Current Therapeutic Strategies", 731, [(BLACK, "2. Current Therapeutic Strategies")], h=11), row("2.1. Non Surgical Approach. For small tears or overuse injuries,", 700, [(ITALIC, "2.1. Non Surgical Approach."), (BODY, "For small tears or overuse injuries,")]), row("physicians opt for conservative therapy such as rest and physiotherapy.", 688, [(BODY, "physicians opt for conservative therapy such as rest and physiotherapy.")])] + body_rows(6, 670)}
    report = restyle(doc, rows)
    assert report["run_in_headings"] == 1 and report["unfused_headings"] == 0
    assert order(doc) == [("section_header", "2. Current Therapeutic Strategies"), ("section_header", "2.1. Non Surgical Approach"), ("text", "For small tears or overuse injuries, physicians opt for conservative therapy such as rest and physiotherapy.")]
    header = doc["texts"][-1]
    assert header["_runin"] and header["prov"][0]["page_no"] == 2 and header["prov"][0]["bbox"]["t"] == 700
    tree = build_tree(doc, "k")
    assert [n.heading for n in tree.walk() if n.type == "section" and n.heading and n.heading != "Front matter"][-2:] == ["2. Current Therapeutic Strategies", "2.1. Non Surgical Approach"]
    assert tree.repairs.get("run_in_headings") is None  # counted only through the recovery report, which the document carries
    doc["_recovery"] = report
    assert build_tree(doc, "k").repairs["run_in_headings"] == 1


def test_a_heading_fused_into_a_paragraph_is_cut_out_after_the_sentence_before_it():
    para = "Minerals were deposited in three cycles as shown in Figure 3. 2.3. Mineralization by Cells Osteoblasts seeded on the scaffolds deposited minerals of their own over four weeks."
    texts = [item(0, "text", para, 2, 54, 640, 290, 700)]
    doc = doc_of(texts)
    rows = {2: [row("Minerals were deposited in three cycles as shown in Figure 3.", 700, [(BODY, "Minerals were deposited in three cycles as shown in Figure 3.")]), row("2.3. Mineralization by Cells", 686, [(BOLD, "2.3. Mineralization by Cells")]), row("Osteoblasts seeded on the scaffolds deposited minerals of their own", 672, [(BODY, "Osteoblasts seeded on the scaffolds deposited minerals of their own")]), row("over four weeks.", 660, [(BODY, "over four weeks.")])] + body_rows(6, 640)}
    report = restyle(doc, rows)
    assert report["unfused_headings"] == 1
    assert order(doc) == [("text", "Minerals were deposited in three cycles as shown in Figure 3."), ("section_header", "2.3. Mineralization by Cells"), ("text", "Osteoblasts seeded on the scaffolds deposited minerals of their own over four weeks.")]
    # a bold phrase inside a sentence is emphasis, not a heading
    para2 = "The strongest effect was seen with genipin crosslinking of the fibres, which doubled the modulus."
    doc2 = doc_of([item(0, "text", para2, 2, 54, 680, 290, 700)])
    rows2 = {2: [row("The strongest effect was seen with", 700, [(BODY, "The strongest effect was seen with")]), row("genipin crosslinking", 688, [(BOLD, "genipin crosslinking")]), row("of the fibres, which doubled the modulus.", 676, [(BODY, "of the fibres, which doubled the modulus.")])] + body_rows(6, 660)}
    assert restyle(doc2, rows2)["unfused_headings"] == 0 and order(doc2) == [("text", para2)]


def test_a_headings_letters_are_put_back_as_the_row_prints_them_and_a_whole_bold_row_read_as_text_is_a_heading():
    texts = [item(0, "section_header", "1 | I NTRODUCTION", 2, 54, 720, 200, 731), item(1, "text", "4.3. Simulation Analysis Method", 2, 54, 700, 200, 710), item(2, "text", "Finite element models of the fibril were built from the reconstructions and loaded in tension.", 2, 54, 660, 290, 698)]
    doc = doc_of(texts)
    rows = {2: [row("1 | INTRODUCTION", 731, [(BOLD, "1 | INTRODUCTION")], h=11), row("4.3. Simulation Analysis Method", 710, [(BOLD, "4.3. Simulation Analysis Method")]), row("Finite element models of the fibril were built from the reconstructions", 698, [(BODY, "Finite element models of the fibril were built from the reconstructions")]), row("and loaded in tension.", 686, [(BODY, "and loaded in tension.")])] + body_rows(6, 670)}
    report = restyle(doc, rows)
    assert report["retexted_headings"] == 1 and report["unfused_headings"] == 1
    assert order(doc)[:2] == [("section_header", "1 | INTRODUCTION"), ("section_header", "4.3. Simulation Analysis Method")] and doc["texts"][0]["_restyled"] and doc["texts"][1]["_restyled"]


def test_a_dropped_bold_row_comes_back_as_a_heading_and_an_italic_one_does_not():
    texts = [item(0, "text", "Flow sorted cells were expanded in culture and seeded on scaffolds for surgery at passage 5.", 2, 54, 640, 290, 668)]
    doc = doc_of(texts)
    rows = {2: [row("Cell seeding of scaffolds", 690, [(BOLD, "Cell seeding of scaffolds")]), row("Staphylococcus aureus", 680, [(ITALIC, "Staphylococcus aureus")]), row("Flow sorted cells were expanded in culture and seeded on", 668, [(BODY, "Flow sorted cells were expanded in culture and seeded on")]), row("scaffolds for surgery at passage 5.", 656, [(BODY, "scaffolds for surgery at passage 5.")])] + body_rows(6, 640)}
    report = restyle(doc, rows)
    assert report["recovered_headings"] == 1
    assert order(doc) == [("section_header", "Cell seeding of scaffolds"), ("text", "Flow sorted cells were expanded in culture and seeded on scaffolds for surgery at passage 5.")]
    assert doc["texts"][-1]["_recovered_heading"]


def test_a_series_of_bold_names_is_left_alone_but_the_statements_the_vocabulary_knows_are_cut():
    names = ["Zhongliang Lang: Conceptualization, Methodology, Writing – original draft.", "Miao Zhang: Investigation, Data curation, Visualization.", "Chunyi Wen: Supervision, Funding acquisition, Writing – review and editing."]
    statements = ["Funding: This work was supported by the National Natural Science Foundation of China.", "Data Availability Statement: The data presented in this study are available on request.", "Conflicts of Interest: The authors declare no conflict of interest."]
    texts = [item(i, "text", t, 2, 54, 700 - 30 * i - 20, 290, 700 - 30 * i) for i, t in enumerate(names + statements)]
    doc = doc_of(texts)
    rows = {2: []}
    for i, t in enumerate(names + statements):
        head, rest = t.split(":", 1)
        rows[2].append(row(t[:70], 700 - 30 * i, [(BOLD, head + ":"), (BODY, rest[: 70 - len(head) - 1])]))
    rows[2] += body_rows(6, 500)
    report = restyle(doc, rows)
    assert report["run_in_headings"] == 3
    got = order(doc)
    assert [t for label, t in got if label == "section_header"] == ["Funding", "Data Availability Statement", "Conflicts of Interest"]
    assert got[0] == ("text", names[0]) and ("text", "This work was supported by the National Natural Science Foundation of China.") in got


# -- a heading's depth from its look ----------------------------------------------------------------

def heading_row(text, t, style, cap):
    # one run of one style; every capital and digit the given height on the page
    x = text.replace(" ", "")
    return Row(text, 54, t - cap, min(290, 54 + 4.0 * len(text)), t, [(style, x)], [[cap] * sum(ch.isupper() or ch.isdigit() for ch in x)])


def paper(heads):
    """A page of headings, each with a paragraph under it: (text, style, capital height)."""
    texts, rows, t, i = [], [], 760, 0
    para = "Collagen fibrils were aligned along the axis of the scaffold, and the cells followed them over two weeks."
    for text, style, cap in heads:
        texts.append(item(i, "section_header", text, 2, 50, t - cap - 2, 300, t + 2))
        rows.append(heading_row(text, t, style, cap))
        texts.append(item(i + 1, "text", para, 2, 50, t - 40, 300, t - 14))
        rows.append(row(para[:60], t - 16, [(BODY, para[:60])]))
        rows.append(row(para[60:], t - 28, [(BODY, para[60:])]))
        t, i = t - 48, i + 2
    return doc_of(texts), {2: rows}


TOP = ("Minion-Bold", 700, 9.0)
SUB = ("Minion-Bold", 700, 8.0)
SUBIT = ("Minion-BoldItalic", 700, 7.5)


def test_a_heading_set_like_the_core_sections_is_top_level_and_one_set_less_prominently_is_nested():
    from litrag_parser.typography import depth_by_type

    doc, rows = paper([("INTRODUCTION", TOP, 7.0), ("TENDON BIOLOGY", TOP, 7.0), ("Cells in the tendon", SUB, 6.2), ("GROWTH FACTORS", TOP, 7.0), ("Cells", SUB, 6.2), ("CONCLUSIONS", TOP, 7.0)])
    report: dict = {}
    depth_by_type(doc, rows, report)
    levels = {t["text"]: t.get("_typo_level") for t in doc["texts"] if t["label"] == "section_header"}
    assert levels == {"INTRODUCTION": 1, "TENDON BIOLOGY": 1, "Cells in the tendon": 2, "GROWTH FACTORS": 1, "Cells": 2, "CONCLUSIONS": 1} and report["depth_by_type"] == 6
    tree = build_tree(doc, "k")
    tops = [(n.heading, n.role) for n in tree.root.children if n.type == "section" and n.heading not in ("Front matter", "Abstract")]
    # the review's own sections stand at the top level, not under the introduction; "Cells" (a vocabulary word) is a subsection as it is set
    assert tops == [("INTRODUCTION", "introduction"), ("TENDON BIOLOGY", "other"), ("GROWTH FACTORS", "other"), ("CONCLUSIONS", "discussion")]
    growth = next(n for n in tree.root.children if n.heading == "GROWTH FACTORS")
    assert [c.heading for c in growth.children if c.type == "section"] == ["Cells"] and all(c.role == "other" for c in growth.children)


def test_the_type_says_nothing_where_it_does_not_separate_the_levels_or_the_top_level_is_numbered():
    from litrag_parser.typography import depth_by_type

    # numbered subsections set like the top level: the type does not tell the levels apart
    doc, rows = paper([("1. Introduction", TOP, 7.0), ("1.1 Tendon", TOP, 7.0), ("1.2 Ligament", TOP, 7.0), ("Growth factors", TOP, 7.0), ("2. Methods", TOP, 7.0)])
    report: dict = {}
    depth_by_type(doc, rows, report)
    assert report.get("depth_by_type_unsure") == 1 and not any("_typo_level" in t for t in doc["texts"])
    # a numbered top level: an unnumbered heading is no top-level section however it is set (a box's "The Bottom Line")
    doc, rows = paper([("1 INTRODUCTION", TOP, 7.0), ("2 METHODS", TOP, 7.0), ("2.1 Study design", SUB, 6.2), ("The Bottom Line", TOP, 7.6), ("2.2 Analyses", SUB, 6.2), ("3 RESULTS", TOP, 7.0)])
    report = {}
    depth_by_type(doc, rows, report)
    levels = {t["text"]: t.get("_typo_level") for t in doc["texts"] if t["label"] == "section_header"}
    assert levels["The Bottom Line"] != 1  # nested, or left to the rules: never a top-level section
    # a number the top level does not use, set below it, is a list's: nested
    doc, rows = paper([("Introduction", TOP, 7.0), ("Methods", TOP, 7.0), ("1. Feature embedding module", SUBIT, 5.8), ("2. Edge-aware attention layers", SUBIT, 5.8), ("Discussion", TOP, 7.0)])
    report = {}
    depth_by_type(doc, rows, report)
    tree = build_tree(doc, "k")
    methods = next(n for n in tree.root.children if n.heading == "Methods")
    assert [c.heading for c in methods.children if c.type == "section"] == ["1. Feature embedding module", "2. Edge-aware attention layers"]
    assert all(c.role == "methods" for c in methods.children)


def test_a_line_of_capitals_is_a_heading_where_the_paper_sets_its_headings_in_capitals():
    # ASTMH sets its headings in capitals in the body's own face, marked by nothing else. The layout
    # model found three and read the fourth as text; in such a paper that line is a heading too
    heads = ["MONITORING EXPERIENCE PROVIDES RELEVANT PRECEDENTS", "PRERELEASE RISK ASSESSMENT INFORMS THE SCOPE", "COLLECTION AND TESTING METHODS ARE AVAILABLE"]
    missed = "DISCUSSION: A GENERIC FRAMEWORK CAN BE DERIVED"
    texts, rows, t, i = [], [], 700, 0
    para = "Collagen fibrils were aligned along the axis of the scaffold, and the cells followed them over two weeks."
    for k, head in enumerate([*heads, missed]):
        label = "text" if head == missed else "section_header"
        texts.append(item(i, label, head, 2, 54, t - 11, 54 + 4.0 * len(head), t))
        rows.append(row(head, t, [(BODY, head)], r=54 + 4.0 * len(head)))
        texts.append(item(i + 1, "text", para, 2, 54, t - 40, 290, t - 14))
        rows.append(row(para, t - 16, [(BODY, para)]))
        t, i = t - 48, i + 2
    doc = doc_of(texts)
    report = restyle(doc, {2: rows})
    assert report["caps_headings"] == 1
    assert [(x["label"], x["text"]) for x in doc["texts"] if x["text"] == missed] == [("section_header", missed)]
    # and where the paper's headings are set apart in the usual way, capitals say nothing
    plain = doc_of([item(0, "section_header", "TENDON BIOLOGY", 2, 54, 689, 200, 700), item(1, "text", "SOME WORDS IN CAPITALS HERE", 2, 54, 660, 250, 672)])
    r2 = [row("TENDON BIOLOGY", 700, [(BOLD, "TENDON BIOLOGY")]), row("SOME WORDS IN CAPITALS HERE", 672, [(BODY, "SOME WORDS IN CAPITALS HERE")]), *body_rows(8, 650)]
    assert restyle(plain, {2: r2}).get("caps_headings", 0) == 0 and plain["texts"][1]["label"] == "text"


def test_a_paper_with_no_core_section_whose_headings_are_set_one_way_has_one_level():
    from litrag_parser.typography import depth_by_type

    # an editorial under topical headings alone, all set alike: each is top-level, as its JATS file keeps them
    heads = ["Natural selfish genetic elements", "Synthetic gene drives", "Outlook"]
    doc, rows = paper([(h, TOP, 7.0) for h in heads])
    report: dict = {}
    depth_by_type(doc, rows, report)
    assert {t["text"]: t.get("_typo_level") for t in doc["texts"] if t["label"] == "section_header"} == dict.fromkeys(heads, 1)
    assert report["depth_by_type_one_level"] == 3  # the tree they make: test_structure's editorial
    # a second look in the body: the type may separate levels the rules cannot name, and nothing is said
    doc, rows = paper([("Natural selfish genetic elements", TOP, 7.0), ("Meiotic drive", SUBIT, 5.8), ("Synthetic gene drives", TOP, 7.0)])
    report = {}
    depth_by_type(doc, rows, report)
    assert not any("_typo_level" in t for t in doc["texts"]) and "depth_by_type_one_level" not in report
