"""The systemic defects of the corpus sweep, each with the smallest document that shows it:
a paragraph read on both of its pages, superscripts marked, tables without structure,
notes glued to paragraphs, furniture folded into text, geometry that knows where the blocks
sit, displaced tails, cut captions, and the fonts' digits."""

from litrag_parser.glyphs import repair_glyphs
from litrag_parser.recover import Line, _line_of, recover
from litrag_parser.tree import _continues, build_tree


def item(i, label, text, page, l, b, r, t, **extra):
    return {"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "level": 1 if label == "section_header" else None, "prov": [{"page_no": page, "bbox": {"l": l, "t": t, "r": r, "b": b, "coord_origin": "BOTTOMLEFT"}}], **extra}


def doc_of(texts, pictures=(), tables=(), pages=(3, 4)):
    body = [{"$ref": t["self_ref"]} for t in texts if t.get("parent", {}).get("$ref") == "#/body"] + [{"$ref": p["self_ref"]} for p in pictures] + [{"$ref": p["self_ref"]} for p in tables]
    return {"name": "d", "body": {"self_ref": "#/body", "children": body}, "texts": list(texts), "pictures": list(pictures), "tables": list(tables), "groups": [], "pages": {str(p): {"page_no": p, "size": {"width": 600, "height": 800}} for p in pages}}


def _first(ref, texts, pictures, tables):
    for it in list(texts) + list(pictures) + list(tables):
        if it["self_ref"] == ref["$ref"]:
            return it
    raise KeyError(ref)


def lines(page_lines):
    return [Line(t, l, b, r, tp) for t, l, b, r, tp in page_lines]


def paragraphs(tree):
    return [n.text for n in tree.walk() if n.type == "paragraph"]


# ---- recovery: the text layer read back ----


def test_a_paragraph_on_two_pages_is_read_on_both_and_its_second_page_is_not_recovered_again():
    para = item(1, "text", "The samples were fixed in paraformaldehyde and stained with DAPI for the nuclei, then imaged at three magnifications with the same exposure and gain settings.", 3, 54, 500, 290, 524)
    para["prov"].append({"page_no": 4, "bbox": {"l": 54, "t": 740, "r": 290, "b": 716, "coord_origin": "BOTTOMLEFT"}})
    doc = doc_of([item(0, "section_header", "2 Methods", 3, 54, 700, 200, 710), para])
    p3 = lines([("The samples were fixed in paraformaldehyde and stained", 68, 514, 290, 524), ("with DAPI for the nuclei, then imaged at three magnifi-", 54, 502, 290, 512)])
    p4 = lines([("cations with the same exposure and gain settings for all", 54, 730, 290, 740), ("the groups tested.", 54, 718, 120, 728)])
    report = recover(doc, {3: p3, 4: p4})
    assert report["recovered"] == 0  # the second page's lines belong to the paragraph, they are not free
    assert para["_lines"] == 4 and para["_first_indent"] == 14 and para["_last_full"] is False
    assert len(doc["texts"]) == 2


def test_a_superscript_digit_in_the_layer_is_marked_and_a_sliver_of_a_box_is_not():
    def glyph(ch, l, b, h):
        return (ch, (l, b, l + 4, b + h))

    row = [glyph("1", 10, 100, 7), glyph("0", 15, 100, 7), glyph("7", 20, 103.5, 4.5), (" ", None), glyph("c", 30, 100, 5), glyph("e", 35, 100, 5), glyph("l", 40, 100, 7), glyph("l", 45, 100, 7), glyph("s", 50, 100, 5)]
    assert _line_of(row).text == "10^7 cells"
    row = [glyph("m", 10, 100, 5), glyph("l", 15, 100, 7), glyph("−", 22, 104, 0.5), glyph("1", 26, 103.5, 4.5)]
    assert _line_of(row).text == "ml^−1"
    row = [glyph("n", 10, 100, 5), (" ", None), glyph("=", 16, 102, 2), (" ", None), glyph("1", 24, 102, 0.6), glyph("2", 28, 100, 7)]
    assert _line_of(row).text == "n = 12"  # pdfium gave the 1 a sliver of a box above the baseline: not a superscript
    row = [glyph("w", 10, 100, 5), glyph("e", 15, 100, 5), glyph("l", 20, 100, 7), glyph("l", 25, 100, 7), glyph("-", 30, 102.5, 0.5), glyph("d", 35, 100, 7), glyph("e", 40, 100, 5)]
    assert _line_of(row).text == "well-de"  # a hyphen sits at mid-height: never a superscript


def test_a_table_without_structure_gets_its_rows_from_the_layer():
    table = {"self_ref": "#/tables/0", "parent": {"$ref": "#/body"}, "children": [], "label": "table", "captions": [], "data": {"grid": [], "table_cells": []}, "prov": [{"page_no": 3, "bbox": {"l": 54, "t": 600, "r": 290, "b": 560, "coord_origin": "BOTTOMLEFT"}}]}
    doc = doc_of([item(0, "section_header", "3 Results", 3, 54, 700, 200, 710)], tables=[table])
    p3 = lines([("Group", 56, 590, 80, 598), ("Modulus (MPa)", 150, 590, 210, 598), ("Strain (%)", 240, 590, 288, 598), ("Aligned", 56, 576, 84, 584), ("12.4 ± 1.1", 150, 576, 190, 584), ("9.8 ± 0.7", 240, 576, 280, 584), ("Random", 56, 562, 86, 570), ("3.1 ± 0.4", 150, 562, 190, 570), ("14.2 ± 2.0", 240, 562, 284, 570)])
    report = recover(doc, {3: p3})
    assert report["tables"] == 1 and report["recovered"] == 0
    node = next(n for n in build_tree(doc, "k").walk() if n.type == "table")
    assert node.table["cells"] == [["Group", "Modulus (MPa)", "Strain (%)"], ["Aligned", "12.4 ± 1.1", "9.8 ± 0.7"], ["Random", "3.1 ± 0.4", "14.2 ± 2.0"]]


def test_a_table_note_docling_glued_to_a_paragraph_is_split_off_and_the_real_tail_joins():
    head = item(1, "text", "After 1 h of immersion, the swelling ratio of the uncrosslinked filaments was two times that of the cross-linked sample and they Note: The data for the PLA multifilaments were recalculated from [29] for comparison.", 3, 300, 40, 540, 76)
    cut = head["text"].index(" Note:")
    head["prov"][0]["charspan"] = [0, cut]
    head["prov"].append({"page_no": 4, "charspan": [cut + 1, len(head["text"])], "bbox": {"l": 54, "t": 640, "r": 290, "b": 618, "coord_origin": "BOTTOMLEFT"}})
    table = {"self_ref": "#/tables/0", "parent": {"$ref": "#/body"}, "children": [], "label": "table", "captions": [], "data": {"grid": [[{"text": "Thickness"}, {"text": "209"}]]}, "prov": [{"page_no": 4, "bbox": {"l": 54, "t": 740, "r": 290, "b": 646, "coord_origin": "BOTTOMLEFT"}}]}
    tail = item(2, "text", "continued to swell during the 24 h exposure to PBS buffer solution, as expected.", 4, 54, 580, 290, 600)
    doc = doc_of([item(0, "section_header", "3 Results", 3, 300, 700, 500, 710), head, tail], tables=[table])
    doc["body"]["children"] = [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}, {"$ref": "#/tables/0"}, {"$ref": "#/texts/2"}]
    p3 = lines([("After 1 h of immersion, the swelling ratio of the", 300, 66, 540, 76), ("uncrosslinked filaments was two times that of the", 300, 54, 540, 64), ("cross-linked sample and they", 300, 42, 430, 52)])
    p4 = lines([("Thickness 209", 56, 730, 120, 738), ("Note: The data for the PLA multifilaments were", 54, 630, 290, 640), ("recalculated from [29] for comparison.", 54, 618, 220, 628), ("continued to swell during the 24 h exposure to PBS", 54, 590, 290, 600), ("buffer solution, as expected.", 54, 578, 170, 588)])
    report = recover(doc, {3: p3, 4: p4})
    assert report["notes"] == 1
    assert head["text"].endswith("cross-linked sample and they") and len(head["prov"]) == 1
    note = doc["texts"][-1]
    assert note["label"] == "footnote" and note["text"].startswith("Note: The data") and note["prov"][0]["page_no"] == 4
    tree = build_tree(doc, "k")
    assert paragraphs(tree) == ["After 1 h of immersion, the swelling ratio of the uncrosslinked filaments was two times that of the cross-linked sample and they continued to swell during the 24 h exposure to PBS buffer solution, as expected."]
    kinds = [n.type for n in tree.walk() if n.type in ("paragraph", "table", "footnote")]
    assert kinds == ["paragraph", "table", "footnote"]


def test_a_short_block_right_under_a_table_is_its_note_and_never_a_paragraph_s_tail():
    head = item(1, "text", "There are several unique advantages of the current process: (1) no toxic solvents; (2) low cost; (3) practicality of the", 3, 54, 400, 290, 460)
    table = {"self_ref": "#/tables/0", "parent": {"$ref": "#/body"}, "children": [], "label": "table", "captions": [], "data": {"grid": [[{"text": "Tensile stress"}, {"text": "0.8-2.5"}]]}, "prov": [{"page_no": 3, "bbox": {"l": 54, "t": 380, "r": 290, "b": 300, "coord_origin": "BOTTOMLEFT"}}]}
    note = item(2, "text", "Reported values are the minimum and the maximum observations for a given parameter. N = 10 per group.", 3, 54, 278, 290, 296)
    tail = item(3, "text", "experimental set up (pair of electrodes in a humid environment), which can be upgradeable to batch processing.", 3, 300, 400, 540, 460)
    doc = doc_of([item(0, "section_header", "4 Discussion", 3, 54, 700, 200, 710), head, note, tail], tables=[table])
    doc["body"]["children"] = [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}, {"$ref": "#/tables/0"}, {"$ref": "#/texts/2"}, {"$ref": "#/texts/3"}]
    p3 = lines([("There are several unique advantages of the current", 68, 450, 290, 460), ("process: (1) no toxic solvents; (2) low cost; (3)", 54, 438, 290, 448), ("practicality of the", 54, 402, 120, 412),
                ("Tensile stress 0.8-2.5", 56, 370, 160, 378), ("Reported values are the minimum and the maximum", 54, 288, 290, 296), ("observations for a given parameter. N = 10 per group.", 54, 278, 260, 286),
                ("experimental set up (pair of electrodes in a humid", 300, 450, 540, 460), ("environment), which can be upgradeable to batch", 300, 438, 540, 448), ("processing.", 300, 426, 350, 436)])
    report = recover(doc, {3: p3})
    assert report["notes"] == 1 and note["label"] == "footnote"
    tree = build_tree(doc, "k")
    assert paragraphs(tree) == ["There are several unique advantages of the current process: (1) no toxic solvents; (2) low cost; (3) practicality of the experimental set up (pair of electrodes in a humid environment), which can be upgradeable to batch processing."]
    assert tree.repairs.get("joined") == 1  # the note is a footnote, bridgeable like the table: the head stays the anchor
    assert [n.type for n in tree.walk() if n.type in ("paragraph", "table", "footnote")] == ["paragraph", "table", "footnote"]


def test_a_running_head_the_model_folded_into_a_paragraph_is_stripped():
    para = item(1, "text", "www.advmat.de rotating drums were used to wind the threads at a constant speed for all of the groups in this study.", 3, 54, 640, 290, 680)
    doc = doc_of([item(0, "section_header", "2 Methods", 3, 54, 700, 200, 710), para], pages=(3,))
    p3 = lines([("www.advmat.de", 54, 780, 120, 788), ("rotating drums were used to wind the threads at a", 54, 670, 290, 680), ("constant speed for all of the groups in this study.", 54, 658, 290, 668)])
    report = recover(doc, {3: p3})
    assert report["furniture"] == 1
    assert para["text"] == "rotating drums were used to wind the threads at a constant speed for all of the groups in this study."


# ---- the tree: joins that know where the blocks sit ----


def _pdf_doc(items, indents=0.7):
    doc = doc_of(items, pages=(3, 4))
    doc["_indents"] = indents
    return doc


def test_geometry_joins_at_a_column_break_but_has_no_word_for_blocks_far_apart_in_one_column():
    a = item(1, "text", "The cell migration assay was modified from the earlier work and applied to every group of samples in the study.", 3, 54, 300, 290, 360, _last_full=True, _lines=5)
    b = item(2, "text", "Sample sizes in treatment groups were uneven, limiting the comparisons that could be drawn from the histology.", 3, 300, 600, 540, 660, _first_indent=0.0, _lines=5)
    tree = build_tree(_pdf_doc([item(0, "section_header", "3 Results", 3, 54, 700, 200, 710), a, b]), "k")
    assert len(paragraphs(tree)) == 1 and tree.repairs.get("joined") == 1  # the other column: the page says continue
    c = item(2, "text", "Clinical Relevance: This study demonstrates the feasibility of the approach in a rabbit model of the injury.", 3, 54, 100, 290, 160, _first_indent=0.0, _lines=5)
    tree = build_tree(_pdf_doc([item(0, "section_header", "3 Results", 3, 54, 700, 200, 710), a, c]), "k")
    assert len(paragraphs(tree)) == 2  # far below in the same column, and a label opens it
    d = item(2, "text", "The wall thickness of the grafts was measured and compared between the two prototypes on the same day.", 3, 54, 236, 290, 296, _first_indent=0.0, _lines=5)
    tree = build_tree(_pdf_doc([item(0, "section_header", "3 Results", 3, 54, 700, 200, 710), a, d]), "k")
    assert len(paragraphs(tree)) == 1  # right under it, flush left in a paper that indents: one paragraph
    e = item(2, "text", "Keywords Diverse intelligence · Basal cognition · Problem spaces · Search efficiency", 4, 54, 700, 290, 720, _first_indent=0.0, _lines=2)
    tree = build_tree(_pdf_doc([item(0, "section_header", "Abstract", 3, 54, 700, 200, 710), a, e]), "k")
    assert len(paragraphs(tree)) == 1  # a run-in label at the top of the next page is a new block — and keywords after the abstract are front matter
    assert [(n.label, n.text[:8]) for n in tree.walk() if n.type == "meta"] == [("keywords", "Keywords")]


def test_a_lowercase_tail_never_continues_a_finished_sentence():
    assert _continues("The samples were imaged.", "degree is the search efficiency of the system.", 3, 3, True) is False
    assert _continues("The samples were imaged and the", "degree of alignment was measured.", 3, 3, None) is True


def test_the_end_of_a_paragraph_read_twice_is_dropped():
    a = item(1, "text", "To a lesser degree is the search efficiency of the system within a given problem space.", 3, 54, 600, 290, 640)
    b = item(2, "text", "degree is the search efficiency of the system within a given problem space.", 3, 54, 590, 290, 604)
    tree = build_tree(doc_of([item(0, "section_header", "3 Results", 3, 54, 700, 200, 710), a, b], pages=(3,)), "k")
    assert paragraphs(tree) == [a["text"]] and tree.repairs.get("deduplicated") == 1


def test_a_caption_cut_by_its_figure_takes_the_tail_that_follows_the_figure():
    picture = {"self_ref": "#/pictures/0", "parent": {"$ref": "#/body"}, "children": [{"$ref": "#/texts/1"}], "label": "picture", "captions": [{"$ref": "#/texts/1"}], "prov": [{"page_no": 3, "bbox": {"l": 54, "t": 700, "r": 540, "b": 400, "coord_origin": "BOTTOMLEFT"}}]}
    caption = {"self_ref": "#/texts/1", "parent": {"$ref": "#/pictures/0"}, "children": [], "label": "caption", "text": "Fig. 1. (a) Schematic of the", "_last_full": True, "prov": [{"page_no": 3, "bbox": {"l": 54, "t": 396, "r": 290, "b": 388, "coord_origin": "BOTTOMLEFT"}}]}
    tail = item(2, "text", "experimental set up (pair of electrodes in a humid environment) used for the alignment of collagen.", 3, 300, 380, 540, 396)
    doc = doc_of([item(0, "section_header", "2 Methods", 3, 54, 760, 200, 770), caption, tail], pictures=[picture], pages=(3,))
    doc["body"]["children"] = [{"$ref": "#/texts/0"}, {"$ref": "#/pictures/0"}, {"$ref": "#/texts/2"}]
    tree = build_tree(doc, "k")
    cap = next(n for n in tree.walk() if n.type == "caption")
    assert cap.text == "Fig. 1. (a) Schematic of the experimental set up (pair of electrodes in a humid environment) used for the alignment of collagen."
    assert paragraphs(tree) == [] and tree.repairs.get("caption_tail") == 1


# ---- the fonts ----


def test_control_codes_from_the_symbol_font_are_read_by_their_contexts():
    assert repair_glyphs("incubated at 37 \x0e C and p \x14 0.05, tears with \x15 5 cm of retraction (2.3 \x06 0.3)") == "incubated at 37 °C and p ≤ 0.05, tears with ≥ 5 cm of retraction (2.3 ± 0.3)"
    assert repair_glyphs("Keywords Tendon \x01 Ligament \x01 Stem cell") == "Keywords Tendon · Ligament · Stem cell"
    assert repair_glyphs("fragments ( \x18 1 cm length) at pH \x19 7-8; Or \x13 efice R") == "fragments ( ~1 cm length) at pH ≈7-8; Oréfice R"
    assert repair_glyphs("computed using the equation: \x12 \x13") == "computed using the equation:"


def test_the_oldest_wiley_files_map_symbols_onto_digits():
    assert repair_glyphs("stored at 37 8 C for 7 days; the mass was 0.59 6 0.06 g ( N 5 3 threads), washed in 1 3 PBS ( P \\ .05)") == "stored at 37 °C for 7 days; the mass was 0.59 ± 0.06 g ( N = 3 threads), washed in 1× PBS ( P < .05)"
    assert repair_glyphs("the 960 cm 2 1 peak; a 63 3 Leica objective; IL-6 1 cells; a score of 0 5 no presence and 4 5 extensive presence") == "the 960 cm^-1 peak; a 63× Leica objective; IL-6+ cells; a score of 0 = no presence and 4 = extensive presence"
    assert repair_glyphs("incubated at 37 1 C") == "incubated at 37 °C"


def test_exponents_and_digits_set_apart_are_closed_up():
    assert repair_glyphs("seeded at 1 × 10 6 cells; on the order of 10 5 neoblasts; between 10 4 and 10 6 synapses; diluted by 10 2 and 10 4 folds") == "seeded at 1 × 10^6 cells; on the order of 10^5 neoblasts; between 10^4 and 10^6 synapses; diluted by 10^2 and 10^4 folds"
    assert repair_glyphs("over 10,0 0 0 repeats and 80 0 0 kDa (Science 30 0 (5625) (20 03))") == "over 10,000 repeats and 8000 kDa (Science 300 (5625) (2003))"
    assert repair_glyphs("Table 10 5 samples; ID 10 4; samples 1 - 10 were pooled") == "Table 10 5 samples; ID 10 4; samples 1 - 10 were pooled"


def test_ligatures_the_font_split_are_mended_by_the_paper_s_own_words():
    assert repair_glyphs("signi fi cantly higher; were fi xed; speci fi c binding; the fi rst step; sti ff ness; cut o ff value; in fl uence; re fi ned; between fl at sheets; Fi rst,") == "significantly higher; were fixed; specific binding; the first step; stiffness; cut off value; influence; refined; between flat sheets; First,"
    assert repair_glyphs("with  increasing  fi  ber  diameter") == "with  increasing  fiber  diameter"  # Wiley's double spaces


# ---- the papers that did not audit clean ----


def test_a_colon_before_a_list_or_a_prose_equation_is_not_a_missing_equation():
    from litrag_parser.audit import audit_tree

    def kinds(*texts):
        doc = doc_of([item(i, "section_header" if i == 0 else "text", t, 3, 54, 700 - 30 * i, 290, 720 - 30 * i) for i, t in enumerate(("2. Methods",) + texts)], pages=(3,))
        return sorted({f.kind for f in audit_tree(build_tree(doc, "k")) if f.severity == "error"})

    assert kinds("The challenges for myocyte-driven robots are as follows:", "1. The ethical issues of primary myocytes need care.") == []
    assert kinds("The tensile strength is calculated using the following formula:", "Tensile strength = Break load/Strip cross-sectional area") == []
    assert kinds("The amount of DOX was calculated using the following formula:", "where A is the absorbance of the control.") == ["missing-equation"]


def test_formulas_docling_would_drop_reach_it_as_tex_math():
    from lxml import etree

    from litrag_parser.jats_prep import prepare_jats

    latex = r"\documentclass[12pt]{minimal} \usepackage{amsmath} \begin{document}$$Absorption{\text{ }}rate = \left( {W_1 - W_0} \right)/W_0 \times 100$$\end{document}"
    xml = ('<article xmlns:xlink="http://www.w3.org/1999/xlink"><body>'
           '<p>The rate was calculated as follows:</p><p id="Par27"> <disp-formula id="Equa"><tex-math id="d1">' + latex + '</tex-math></disp-formula></p>'
           '<disp-formula id="e"><inline-formula>(<italic>η</italic><sub>sp</sub>/<italic>c</italic>)<italic><sub>t</sub></italic></inline-formula></disp-formula>'
           '<disp-formula id="g"><label>(2)</label><graphic xlink:href="ADVS-13-e18807-e001.jpg"/></disp-formula>'
           '<disp-formula id="a"><alternatives><graphic xlink:href="M1.gif"/><tex-math id="M1">$$E = F/A$$</tex-math></alternatives></disp-formula>'
           '<p>Light absorbed is given by <inline-formula><italic>ϕ</italic><sub>ex</sub> = <italic>ϕ</italic><sub>in</sub> × e<sup>(−kd)</sup></inline-formula> where d is the depth.</p>'
           '<ref-list><ref id="r1"><mixed-citation>&gt;Yeh, Y. H. Inflammatory interferon. J. Exp. 37, 70 (2018).</mixed-citation></ref></ref-list>'
           '</body></article>').encode("utf-8")
    root = etree.fromstring(prepare_jats(xml))
    texs = ["".join(t.itertext()) for t in root.iter("tex-math")]
    assert texs[0] == r"Absorption rate = \left( {W_1 - W_0} \right)/W_0 \times 100"  # the LaTeX document cut to its maths
    lifted = root.find(".//disp-formula[@id='Equa']").getparent()
    assert lifted.tag == "sec" and lifted.find("title").text == "Main text"  # lifted out of the empty <p>; loose body paragraphs wrapped in a section
    assert "(η_sp/c)_t" in texs[1]  # italic and subscript markup set as a line
    assert texs[2] == "[equation as image: ADVS-13-e18807-e001.jpg]"  # its place marked, its text not there
    assert texs[3] == "E = F/A" and root.find(".//disp-formula[@id='a']/alternatives") is None
    para = " ".join("".join(root.findall(".//p")[-1].itertext()).split())
    assert "given by ϕ_ex = ϕ_in × e^(−kd) where d" in para
    assert "".join(root.find(".//mixed-citation").itertext()).startswith("Yeh, Y. H.")


def test_fragments_beside_figures_and_equations_read_as_text():
    picture = {"self_ref": "#/pictures/0", "parent": {"$ref": "#/body"}, "children": [], "label": "picture", "captions": [], "prov": [{"page_no": 3, "bbox": {"l": 54, "t": 600, "r": 290, "b": 400, "coord_origin": "BOTTOMLEFT"}}]}
    items = [
        item(0, "section_header", "3. Results", 3, 54, 700, 200, 710),
        item(1, "text", "Striae distensae are dermal scars.", 3, 54, 660, 290, 690),
        item(2, "text", "Δ", 3, 54, 380, 60, 390),
        item(3, "text", "EI difference (Posttreatment EI - Initial EI) rose in every site.", 3, 54, 360, 290, 378),
        item(4, "text", "-5", 3, 54, 340, 62, 350),
    ]
    doc = doc_of(items, pictures=[picture], pages=(3,))
    doc["body"]["children"] = [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}, {"$ref": "#/pictures/0"}, {"$ref": "#/texts/2"}, {"$ref": "#/texts/3"}, {"$ref": "#/texts/4"}, {"$ref": "#/pictures/0"}]
    tree = build_tree(doc, "k")
    assert paragraphs(tree) == ["Striae distensae are dermal scars.", "ΔEI difference (Posttreatment EI - Initial EI) rose in every site."]
    assert tree.repairs.get("stitched") == 1 and tree.repairs.get("junk") == 1
    items = [
        item(0, "section_header", "2. Methods", 3, 54, 700, 200, 710),
        item(1, "formula", "Extermal diameter flat width 2× = (3)", 3, 54, 660, 290, 680),
        item(2, "text", "= × Internal diameter External diameter thickness 2 (4)", 3, 54, 630, 290, 650),
        item(3, "text", "where t = the wall thickness of the graft, in mm.", 3, 54, 600, 290, 620),
        item(4, "text", "= the measured pressurized external diameter, in mm;", 3, 54, 570, 290, 590),
        item(5, "text", "Rp = the pressurized internal radius, in mm; Dp = the measured pressurized external diameter, in mm;", 3, 54, 540, 290, 560),
    ]
    tree = build_tree(doc_of(items, pages=(3,)), "k")
    assert [n.type for n in tree.walk() if n.type in ("formula", "paragraph")] == ["formula", "formula", "paragraph", "paragraph"]
    assert tree.repairs.get("formula_text") == 1 and tree.repairs.get("deduplicated") == 1


def test_a_reference_entry_split_after_its_journal_abbreviation_is_one_entry():
    assert _continues("X. Sun, Y. Mao, Z. Yu, P. Yang, F. Jiang, Adv. Mater.", ", 2400084.", 36, 36) is True
    assert _continues("The samples were imaged.", ", and counted.", 3, 3) is False
