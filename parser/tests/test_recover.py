"""The text layer read back: lines no box holds, a box missing lines, an empty equation,
the geometry of a block — on lines made by hand, no PDF needed."""

from litrag_parser.recover import Line, _join_lines, clean, recover
from litrag_parser.tree import build_tree


def item(i, label, text, page, l, b, r, t):
    return {"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "prov": [{"page_no": page, "bbox": {"l": l, "t": t, "r": r, "b": b, "coord_origin": "BOTTOMLEFT"}}]}


def doc_of(texts, pictures=()):
    body = [{"$ref": t["self_ref"]} for t in texts] + [{"$ref": p["self_ref"]} for p in pictures]
    return {"name": "d", "body": {"self_ref": "#/body", "children": body}, "texts": list(texts), "pictures": list(pictures), "tables": [], "groups": [], "pages": {"3": {"page_no": 3, "size": {"width": 600, "height": 800}}, "4": {"page_no": 4, "size": {"width": 600, "height": 800}}}}


def lines(page_lines):
    # (text, l, b, r, t) rows top to bottom
    return [Line(t, l, b, r, tp) for t, l, b, r, tp in page_lines]


def test_the_top_of_a_column_the_model_missed_comes_back_before_the_heading():
    texts = [
        item(0, "section_header", "Cell seeding of scaffolds", 4, 54, 661, 152, 669),
        item(1, "text", "Flow sorted cells were expanded in culture and seeded on scaffolds for surgery at passage 5.", 4, 54, 600, 290, 657),
    ]
    doc = doc_of(texts)
    page4 = lines([
        ("a density of 107 cells/mL and flow sorted (BD FACSAria", 54, 712, 290, 722),
        ("SORP, Becton-Dickinson) to select for cells that were CD44+,", 55, 700, 290, 709),
        ("CD45-, and CD90-.", 55, 690, 131, 697),
        ("Cell seeding of scaffolds", 54, 661, 152, 669),
        ("Flow sorted cells were expanded in culture and seeded on", 55, 649, 290, 657),
        ("scaffolds for surgery at passage 5.", 54, 638, 290, 646),
    ])
    report = recover(doc, {4: page4})
    assert report["recovered"] == 1 and report["rebuilt"] == 0
    new = doc["texts"][-1]
    assert new["text"] == "a density of 107 cells/mL and flow sorted (BD FACSAria SORP, Becton-Dickinson) to select for cells that were CD44+, CD45-, and CD90-."
    assert new["prov"][0]["page_no"] == 4 and new["_recovered"] and new["_lines"] == 3 and new["_last_full"] is False
    # in the body it comes before the heading it sits above
    assert [c["$ref"] for c in doc["body"]["children"]] == ["#/texts/2", "#/texts/0", "#/texts/1"]
    tree = build_tree(doc, "k")
    assert [n.text[:20] for n in tree.walk() if n.type == "paragraph"] == ["a density of 107 cel", "Flow sorted cells we"]


def test_a_cut_tail_rejoins_its_block_and_a_footer_that_is_prose_is_kept():
    texts = [
        item(0, "text", "The bones were rongeured off. The femurs were placed with open, distal end", 3, 54, 500, 290, 560),
        item(1, "page_footer", "Small-angle X-ray scattering (SAXS), and second harmonic generation (SHG) analyses were performed on the same samples.", 3, 54, 60, 290, 80),
    ]
    doc = doc_of(texts)
    page3 = lines([
        ("The bones were rongeured off. The femurs were placed with open, distal end", 54, 550, 290, 560),
        ("downward in 50 mL centrifuge tubes and spun at 2,000 rpm (600 g) for 1 min to extract marrow.", 54, 488, 290, 497),
        ("Small-angle X-ray scattering (SAXS), and second harmonic generation (SHG) analyses were", 54, 70, 290, 80),
        ("performed on the same samples to confirm the alignment of the collagen fibrils.", 54, 60, 290, 69),
    ])
    report = recover(doc, {3: page3})
    assert report["attached"] == 1 and doc["texts"][0]["text"].endswith("distal end downward in 50 mL centrifuge tubes and spun at 2,000 rpm (600 g) for 1 min to extract marrow.")
    assert report["recovered"] == 1 and doc["texts"][-1]["text"].startswith("Small-angle X-ray scattering")
    tree = build_tree(doc, "k")
    assert [n.type for n in tree.walk() if n.type != "document"] == ["section", "paragraph", "paragraph"]  # the footer's prose is a paragraph, not furniture


def test_a_block_missing_a_line_is_rebuilt_and_an_empty_formula_reads_its_box():
    texts = [
        item(0, "text", "These prior data demonstrating mechanical robustness motivated a preliminary study of the scaffold in vivo.", 3, 54, 500, 290, 540),
        item(1, "formula", "", 3, 100, 440, 250, 460),
    ]
    doc = doc_of(texts)
    page3 = lines([
        ("These prior data demonstrating mechanical robustness", 54, 530, 290, 540),
        ("and tenoinductivity of ELAC in vitro and in vivo", 54, 518, 290, 528),
        ("motivated a preliminary study of the scaffold in vivo.", 54, 506, 200, 516),
        ("K = 1/ln 10 ln(t_bli / t)", 100, 445, 250, 458),
    ])
    report = recover(doc, {3: page3})
    assert report["rebuilt"] == 1 and report["formulas"] == 1
    assert doc["texts"][0]["text"] == "These prior data demonstrating mechanical robustness and tenoinductivity of ELAC in vitro and in vivo motivated a preliminary study of the scaffold in vivo."
    assert doc["texts"][0]["_first_indent"] == 0 and doc["texts"][0]["_last_full"] is False
    assert doc["texts"][1]["text"] == "K = 1/ln 10 ln(t_bli / t)"


def test_running_heads_and_figure_labels_are_not_recovered():
    doc = doc_of([item(0, "text", "Some body text of the page that is long enough to be a paragraph.", 3, 54, 500, 290, 560)])
    page3 = lines([
        ("JOURNAL OF BIOMEDICAL MATERIALS RESEARCH B: APPLIED BIOMATERIALS vol 107 issue 3", 54, 780, 500, 790),
        ("Some body text of the page that is long enough to be a paragraph.", 54, 550, 290, 560),
        ("A B C D", 320, 300, 400, 310),
        ("0 10 20 30 40 50", 320, 280, 400, 290),
        ("Downloaded from https://onlinelibrary.wiley.com by a university on 1 January 2020", 54, 20, 500, 30),
    ])
    assert recover(doc, {3: page3}) == {"recovered": 0, "rebuilt": 0, "formulas": 0, "attached": 0, "ligatures": 0, "tables": 0, "notes": 0}


def test_text_layer_marks_are_cleaned():
    assert clean("com￾pared to those\x02 on fibronectin") == "compared to those on fibronectin"
    assert _join_lines([Line("higher spare respiratory capacity com-", 0, 20, 100, 30), Line("pared to those on fibronectin.", 0, 8, 100, 18)]) == "higher spare respiratory capacity compared to those on fibronectin."
    assert _join_lines([Line("the ELAC threads (Figure 3A) and", 0, 20, 100, 30), Line("Random threads.", 0, 8, 100, 18)]) == "the ELAC threads (Figure 3A) and Random threads."


def test_geometry_decides_a_continuation_before_any_judge():
    # a paper that indents: a block with a full last line, then a block flush left = one paragraph, full stop or not
    texts = [
        item(0, "section_header", "2 Methods", 3, 54, 700, 200, 710),
        item(1, "text", "The samples were fixed in paraformaldehyde and stained with DAPI for the nuclei, then imaged.", 3, 54, 600, 290, 640),
        item(2, "text", "Statistical analysis used ANOVA with Tukey tests across all groups of samples.", 4, 54, 700, 290, 740),
        item(3, "text", "Results were expressed as mean and standard deviation for all groups tested.", 4, 54, 600, 290, 640),
    ]
    doc = doc_of(texts)
    p3 = lines([("The samples were fixed in paraformaldehyde and stained with", 68, 630, 290, 640), ("DAPI for the nuclei, then imaged. And then more words here", 54, 618, 290, 628), ("to make the last line full width across the column now.", 54, 606, 290, 616)])
    p4 = lines([("Statistical analysis used ANOVA with Tukey tests", 54, 730, 290, 740), ("across all groups of samples. The rest of this line", 54, 718, 290, 728), ("is here to make three lines in the block for the stats.", 54, 706, 200, 716),
                ("Results were expressed as mean and standard deviation", 68, 630, 290, 640), ("for all groups tested. Another line for the indent stat", 54, 618, 290, 628), ("and a third one so that the block counts for the paper.", 54, 606, 200, 616)])
    recover(doc, {3: p3, 4: p4})
    assert doc["_indents"] == 0.67 and doc["texts"][1]["_last_full"] is True and doc["texts"][2]["_first_indent"] == 0
    asked = []
    tree = build_tree(doc, "k", judge=lambda a, b, ctx: asked.append(1) or False)
    paragraphs = [n.text[:30] for n in tree.walk() if n.type == "paragraph"]
    assert paragraphs == ["The samples were fixed in para", "Results were expressed as mean"]  # joined by the page's geometry, not by a model
    assert tree.repairs.get("joined") == 1 and asked == []


def test_a_block_says_what_type_it_is_set_in():
    # the type is how a heading is told from a subheading: the layout model gives both one level
    texts = [
        item(0, "section_header", "Introduction", 3, 54, 700, 130, 710),
        item(1, "text", "Tendon injuries are common and heal slowly, and the repaired tissue rarely regains its strength.", 3, 54, 600, 290, 690),
    ]
    doc = doc_of(texts)
    p3 = lines([("Introduction", 54, 700, 130, 710), ("Tendon injuries are common and heal slowly, and the", 54, 680, 290, 690), ("repaired tissue rarely regains its strength.", 54, 668, 230, 678)])
    p3[0].cap, p3[0].font = 8.2, "GillSans-Bold/700"
    p3[1].cap, p3[1].font = 6.1, "GillSans-Regular/400"
    p3[2].cap, p3[2].font = 6.1, "GillSans-Regular/400"
    recover(doc, {3: p3})
    assert doc["texts"][0]["_cap"] == 8.2 and doc["texts"][0]["_font"] == "GillSans-Bold/700"
    assert doc["texts"][1]["_cap"] == 6.1 and doc["_cap"] == 6.1  # the body's own size, which the headings are read against
