"""Where the reader asks the oracle of meaning, and where it must not: a label the run-in
list does not know, a tail between two open paragraphs, a figure's text children, a
reference list in a shape no pattern knows, a line of front matter the rules have no word
for. Each with the rule that fires first, and the silence when no oracle is configured."""

import json
from pathlib import Path

from litrag_parser import lanes, meaning
from litrag_parser.tree import _label_of, build_tree

from test_repairs import _pdf_doc, doc_of, item, paragraphs

FIXTURES = Path(__file__).parent / "fixtures"


def _kinds(oracle):
    return oracle.summary()["kinds"]


def test_a_label_the_run_in_list_does_not_know_vetoes_a_join_the_page_would_make(fake_oracle):
    a = item(1, "text", "The cell migration assay was modified from the earlier work and applied to every group of samples in the study.", 3, 54, 300, 290, 360, _last_full=True, _lines=5)
    b = item(2, "text", "Associated data The datasets generated during the study are available from the corresponding author on request.", 3, 300, 600, 540, 660, _first_indent=0.0, _lines=5)
    tree = build_tree(_pdf_doc([item(0, "section_header", "3 Results", 3, 54, 700, 200, 710), a, b]), "k")
    assert len(paragraphs(tree)) == 2 and tree.repairs.get("label_veto") == 1
    assert _kinds(fake_oracle)["label"]["named"] == 1
    # a label the list knows is never asked about
    c = item(2, "text", "Keywords Diverse intelligence · Basal cognition · Problem spaces", 3, 300, 600, 540, 660, _first_indent=0.0, _lines=5)
    asked = _kinds(fake_oracle)["label"]["asked"]
    build_tree(_pdf_doc([item(0, "section_header", "3 Results", 3, 54, 700, 200, 710), a, c]), "k")
    assert _kinds(fake_oracle)["label"]["asked"] == asked
    # prose that starts with two capitalised words followed by a lowercase one offers no label (`_label_of` is None)…
    d = item(2, "text", "Sample Sizes in treatment groups were uneven, limiting the comparisons that could be drawn from the histology.", 3, 300, 600, 540, 660, _first_indent=0.0, _lines=5)
    tree = build_tree(_pdf_doc([item(0, "section_header", "3 Results", 3, 54, 700, 200, 710), a, d]), "k")
    assert len(paragraphs(tree)) == 1



def test_the_label_a_block_opens_with():
    assert _label_of("Clinical Relevance: This study demonstrates") == "Clinical Relevance"
    assert _label_of("Data availability The data are available") == "Data availability"
    assert _label_of("Author contributions J.S. designed the study") == "Author contributions"
    assert _label_of("Cells were cultured in DMEM") is None
    assert _label_of("the rest of a sentence, Then another") is None


def test_a_tail_between_two_open_paragraphs_goes_to_the_one_it_is_about(fake_oracle):
    head_a = item(1, "text", "The diameter of the collagen fibrils in the crosslinked scaffolds was measured on the micrographs and the fibril", 3, 54, 600, 290, 660, _lines=5)
    head_b = item(2, "text", "Cell viability on the scaffolds after seven days was assessed with a live/dead assay and the viable", 3, 54, 500, 290, 560, _lines=4)
    closed = item(3, "text", "Every measurement was repeated three times on three independent scaffolds and the mean is reported.", 3, 54, 420, 290, 470, _lines=3)
    tail = item(4, "text", "diameter distribution of the collagen fibrils was compared between the scaffolds by a Mann–Whitney test.", 3, 54, 200, 290, 260, _lines=4)
    doc = doc_of([item(0, "section_header", "3 Results", 3, 54, 700, 200, 710), head_a, head_b, closed, tail], pages=(3,))
    tree = build_tree(doc, "k")
    texts = paragraphs(tree)
    assert texts[0].startswith("The diameter of the collagen fibrils") and texts[0].endswith("Mann–Whitney test.")
    assert texts[1] == head_b["text"] and len(texts) == 3
    assert tree.repairs.get("rejoined") == 1 and tree.repairs.get("rejoined_meaning") == 1
    # without an oracle the nearest open paragraph takes the tail, as before
    lanes._active = None
    texts = paragraphs(build_tree(doc, "k"))
    assert texts[1].startswith("Cell viability") and texts[1].endswith("Mann–Whitney test.")


def test_a_tail_is_never_glued_to_a_front_matter_line_whatever_it_resembles(fake_oracle):
    # a keywords line and a date label end without a full stop and stand in the window; neither is a
    # paragraph a tail can be the rest of, so the tail goes to the open paragraph — with or without an oracle
    head = item(1, "text", "Genetic inversions, a type of structural polymorphism, occur when a part of the genome is flipped and the", 3, 54, 600, 290, 660, _lines=4)
    keywords = item(2, "text", "KEYWORDS inversions, structural variations, TB, fusion proteins, domain switching", 3, 54, 560, 290, 580, _lines=2)
    published = item(3, "text", "Published", 3, 54, 540, 290, 550, _lines=1)
    closed = item(4, "text", "The authors declare no conflict of interest and nothing else of note here.", 3, 54, 500, 290, 520, _lines=2)
    tail = item(5, "text", "inversion events can potentially have a drastic impact on the phenotype of the bacteria [7-14].", 3, 54, 400, 290, 440, _lines=3)
    doc = doc_of([item(0, "section_header", "1 Introduction", 3, 54, 700, 200, 710), head, keywords, published, closed, tail], pages=(3,))
    for with_oracle in (True, False):
        if not with_oracle:
            lanes._active = None
        texts = paragraphs(build_tree(doc, "k"))
        assert texts[0].startswith("Genetic inversions") and texts[0].endswith("[7-14].")
        assert not any(t.startswith("KEYWORDS") and "drastic" in t for t in texts) and not any(t.startswith("Published inversion") for t in texts)


def _figure_doc():
    legend = {"self_ref": "#/texts/1", "parent": {"$ref": "#/pictures/0"}, "children": [], "label": "text", "text": "Figure 2 Representative SEM images of the scaffolds at low and high magnification. Scale bars: 100 µm.", "prov": [{"page_no": 3, "bbox": {"l": 54, "t": 300, "r": 290, "b": 280, "coord_origin": "BOTTOMLEFT"}}]}
    axes = {"self_ref": "#/texts/2", "parent": {"$ref": "#/pictures/0"}, "children": [], "label": "text", "text": "Control PBS Treated Day 1 Day 3 Day 7", "prov": [{"page_no": 3, "bbox": {"l": 54, "t": 400, "r": 290, "b": 390, "coord_origin": "BOTTOMLEFT"}}]}
    picture = {"self_ref": "#/pictures/0", "parent": {"$ref": "#/body"}, "children": [{"$ref": "#/texts/1"}, {"$ref": "#/texts/2"}], "label": "picture", "captions": [], "prov": [{"page_no": 3, "bbox": {"l": 54, "t": 600, "r": 290, "b": 280, "coord_origin": "BOTTOMLEFT"}}]}
    head = item(0, "section_header", "3 Results", 3, 54, 700, 200, 710)
    doc = doc_of([head, legend, axes], pictures=[picture], pages=(3,))
    doc["body"]["children"] = [{"$ref": "#/texts/0"}, {"$ref": "#/pictures/0"}]
    return doc


def test_a_short_legend_the_oracle_is_sure_of_is_adopted_and_a_verdict_never_takes_one_away(fake_oracle):
    def figure():
        doc = _figure_doc()
        doc["texts"][1]["text"] = "Schematic illustration of the fabrication."  # five words: not a legend by length
        doc["texts"][2]["text"] = "The samples were washed three times with PBS and incubated at 37 °C for 24 h before seeding onto the scaffolds."  # prose by every measure
        return doc

    fake_oracle.kinds["figtext"].threshold = 0.5  # the toy embedder is coarse; the plumbing is what is tested
    doc = figure()
    tree = build_tree(doc, "k")
    captions = [n.text for n in tree.walk() if n.type == "caption"]
    assert captions == [doc["texts"][1]["text"], doc["texts"][2]["text"]]  # the short legend by meaning; the long prose by the rule, whatever the verdict
    assert tree.repairs.get("captions_meaning") == 1
    assert _kinds(fake_oracle)["figtext"]["asked"] == 2
    # without an oracle, six words or more are a legend, and five are not
    lanes._active = None
    assert [n.text for n in build_tree(figure(), "k").walk() if n.type == "caption"] == [doc["texts"][2]["text"]]


def test_a_reference_list_in_a_shape_no_pattern_knows_is_found_by_meaning(fake_oracle, monkeypatch):
    entries = [f"{n} Y. Tanaka, H. Müller and T. Nowak, Chem. Soc. Rev., 20{n:02d}, {n}, 1234–1256." for n in range(11, 27)]
    prose = ["The scaffolds were washed three times and incubated at 37 °C for 24 h before the cells were seeded onto them at the chosen density."] * 4
    texts = [("section_header", "4 Discussion", 5)] + [("text", p, 5) for p in prose] + [("text", e, 6) for e in entries]
    items = [{"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "level": 1 if label == "section_header" else None, "prov": [{"page_no": page, "bbox": {"l": 50, "t": 700 - 15 * i, "r": 500, "b": 690 - 15 * i, "coord_origin": "BOTTOMLEFT"}}]} for i, (label, text, page) in enumerate(texts)]
    doc = {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items, "pictures": [], "tables": [], "groups": [], "pages": {"5": {"page_no": 5, "size": {"width": 600, "height": 850}}, "6": {"page_no": 6, "size": {"width": 600, "height": 850}}}}
    tree = build_tree(doc, "k")
    refs = [n for n in tree.walk() if n.type == "section" and n.role == "references"]
    assert len(refs) == 1 and tree.repairs.get("inferred_references") >= 8
    assert _kinds(fake_oracle)["refentry"]["named"] >= 8
    # entries every pattern knows are never asked about
    asked = _kinds(fake_oracle)["refentry"]["asked"]
    for it, n in zip(items[5:], range(1, 17)):
        it["text"] = f"[{n}] Smith JA, Lee CD. A title of a paper. J Biomed Mater Res A. 2019;107:812-821."
    tree = build_tree(doc, "k")
    assert tree.repairs.get("inferred_references") == 16 and _kinds(fake_oracle)["refentry"]["asked"] == asked


def test_a_verdict_never_opens_a_reference_list_over_prose(fake_oracle):
    # a run opens at an entry a pattern knows or at a verdict the next item agrees with: a paragraph the
    # oracle mistakes for an entry, with prose after it, does not open the list early
    tail = "The impurities Smith described in 2019, Chem. Soc. Rev., are the ones we measured here and found in every batch tested."
    entries = [f"[{n}] Smith JA, Lee CD. A title of a paper. J Biomed Mater Res A. 2019;107:812-821." for n in range(1, 11)]
    texts = [("section_header", "4 Discussion", 5), ("text", tail, 5), ("text", "Our findings demonstrate that the scaffolds support growth, in line with earlier reports of the same effect, and the work continues.", 5)] + [("text", e, 6) for e in entries]
    items = [{"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "level": 1 if label == "section_header" else None, "prov": [{"page_no": page, "bbox": {"l": 50, "t": 700 - 15 * i, "r": 500, "b": 690 - 15 * i, "coord_origin": "BOTTOMLEFT"}}]} for i, (label, text, page) in enumerate(texts)]
    doc = {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items, "pictures": [], "tables": [], "groups": [], "pages": {"5": {"page_no": 5, "size": {"width": 600, "height": 850}}, "6": {"page_no": 6, "size": {"width": 600, "height": 850}}}}
    tree = build_tree(doc, "k")
    refs = next(n for n in tree.root.children if n.type == "section" and n.role == "references")
    assert [c.text.split("]")[0] for c in refs.children] == [f"[{n}" for n in range(1, 11)]
    assert any(n.text == tail and n.role == "discussion" for n in tree.walk())


def test_a_paragraph_inside_the_abstract_is_not_moved_on_a_resemblance(fake_oracle):
    texts = [
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "Abstract", 1),
        ("text", "We report a scaffold. " * 12, 1),
        ("text", "This work was made possible by grant R01 AR068426 from the NIH and by a fellowship from the Wellcome Trust.", 1),
        ("section_header", "Introduction", 1),
        ("text", "Tendon injuries are common and heal slowly, which is why scaffolds have been studied for decades in this field of work.", 1),
    ]
    items = [{"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "level": 1 if label == "section_header" else None, "prov": [{"page_no": page, "bbox": {"l": 50, "t": 700 - 20 * i, "r": 500, "b": 690 - 20 * i, "coord_origin": "BOTTOMLEFT"}}]} for i, (label, text, page) in enumerate(texts)]
    doc = {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items, "pictures": [], "tables": [], "groups": [], "pages": {"1": {"page_no": 1, "size": {"width": 600, "height": 850}}}}
    tree = build_tree(doc, "k")
    abstract = next(n for n in tree.root.children if n.type == "section" and n.role == "abstract")
    assert [c.text[:8] for c in abstract.children] == ["We repor", "This wor"]


def test_a_line_of_front_matter_the_rules_have_no_word_for_is_typed_by_meaning(fake_oracle):
    texts = [
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("text", "John A. Smith 1 , Maria García 2 , Wei Zhang 1,*", 1),
        ("text", "This work was made possible by grant R01 AR068426 from the NIH and by a fellowship from the Wellcome Trust.", 1),
        ("section_header", "Introduction", 1),
        ("text", "Tendon injuries are common and heal slowly, which is why scaffolds have been studied for decades in this field of work.", 1),
    ]
    items = [{"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "level": 1 if label == "section_header" else None, "prov": [{"page_no": page, "bbox": {"l": 50, "t": 700 - 20 * i, "r": 500, "b": 690 - 20 * i, "coord_origin": "BOTTOMLEFT"}}]} for i, (label, text, page) in enumerate(texts)]
    doc = {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items, "pictures": [], "tables": [], "groups": [], "pages": {"1": {"page_no": 1, "size": {"width": 600, "height": 850}}}}
    tree = build_tree(doc, "k")
    assert [(n.label, n.text[:9]) for n in tree.walk() if n.type == "meta"] == [("authors", "John A. S"), ("funding", "This work")]
    assert tree.repairs.get("front_meaning") == 1
    lanes._active = None
    tree = build_tree(doc, "k")
    assert [(n.label, n.text[:9]) for n in tree.walk() if n.type == "meta"] == [("authors", "John A. S"), ("other", "This work")]


def test_a_journal_name_set_as_a_heading_on_the_first_page_is_front_matter_not_a_section(fake_oracle):
    texts = [
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "Abstract", 1),
        ("text", "We report a scaffold. " * 12, 1),
        ("section_header", "Journal of Materials Chemistry B", 1),
        ("section_header", "Introduction", 1),
        ("text", "Tendon injuries are common and heal slowly, which is why scaffolds have been studied for decades in this field of work.", 1),
        ("section_header", "RESEARCH ARTICLE", 2),
        ("text", "The body has begun: a heading here is never demoted, whatever it resembles, so this one stands as a section.", 2),
    ]
    items = [{"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "level": 1 if label == "section_header" else None, "prov": [{"page_no": page, "bbox": {"l": 50, "t": 700 - 20 * i, "r": 500, "b": 690 - 20 * i, "coord_origin": "BOTTOMLEFT"}}]} for i, (label, text, page) in enumerate(texts)]
    doc = {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items, "pictures": [], "tables": [], "groups": [], "pages": {"1": {"page_no": 1, "size": {"width": 600, "height": 850}}, "2": {"page_no": 2, "size": {"width": 600, "height": 850}}}}
    tree = build_tree(doc, "k")
    headings = [n.heading for n in tree.walk() if n.type == "section"]
    assert headings == ["Abstract", "Front matter", "Introduction", "RESEARCH ARTICLE"]
    assert [(n.label, n.text) for n in tree.walk() if n.type == "meta"] == [("notice", "Journal of Materials Chemistry B")]


def test_an_oracle_that_cannot_answer_gives_the_tree_no_oracle_would(monkeypatch):
    import urllib.request

    from test_structure import _headingless

    plain = build_tree(_headingless(), "k").to_dict()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("connection refused")))  # the real `_embed`, against an Ollama that is not there
    lanes.configure(None)
    assert build_tree(_headingless(), "k").to_dict() == plain
    assert lanes.active().summary()["down"] is True and "connection refused" in lanes.active().summary()["error"]
    # a reply with the wrong number of vectors is the same as no reply, and nothing is stored
    monkeypatch.setattr(meaning.Oracle, "_embed", lambda self, texts: [[1.0, 0.0]] if len(texts) > 1 else None)
    o = lanes.configure(None)
    o.nearest("heading", "strengths and limitations")
    assert o.recall("heading", "strengths and limitations") is None


def test_with_no_oracle_nothing_is_ever_embedded(monkeypatch):
    calls = []
    monkeypatch.setattr(meaning.Oracle, "_embed", lambda self, texts: (calls.append(texts), None)[1])
    doc = json.loads((FIXTURES / "PMC11278924.docling.json").read_text("utf-8"))
    build_tree(doc, "k")
    build_tree(_figure_doc(), "k")
    assert calls == [] and lanes.active() is None
