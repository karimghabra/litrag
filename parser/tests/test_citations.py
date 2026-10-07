"""In-text citations linked to the reference list: numbered (the Micromachines JATS) and
author–year (a made-up paper in Springer's style)."""

import json
from pathlib import Path

from litrag_parser.citations import enrich_from_jats, find_citations, first_surname, link_citations, reference_entries, summarize
from litrag_parser.store import cited_by, cites_of, file_paper, open_store, paper_tree, refs_of, save_refs, save_tree
from litrag_parser.tree import build_tree

FIXTURES = Path(__file__).parent / "fixtures"


def jats_tree():
    return build_tree(json.loads((FIXTURES / "PMC11278924.jats.docling.json").read_text("utf-8")), "doi:10.3390/mi15070851")


def test_first_surname():
    assert first_surname("Adams, F. (2018). Cognition wars.") == "Adams"
    assert first_surname("Wang Y., Wang Z., Dong Y. Collagen-Based Biomaterials.") == "Wang"
    assert first_surname("Parenteau-Bareil R., Gauvin R. Collagen.") == "Parenteau-Bareil"
    assert first_surname("van der Berg, J., & Smith, K. (2001).") == "van der Berg"
    assert first_surname("Taufalele P.V., VanderBurgh J.A. Fiber Alignment.") == "Taufalele"
    assert first_surname("Fields, C., & Levin, M. (2022). Competency in navigating.") == "Fields"


def test_numbered_entries_from_the_jats_tree_and_xml():
    tree = jats_tree()
    refs = reference_entries(tree)
    assert len(refs) == 61 and [r.ref_no for r in refs][:3] == [1, 2, 3]
    assert refs[0].first_author == "Wang" and refs[0].year == "2023" and refs[0].doi == "10.1021/acsbiomaterials.2c00730"
    assert all(r.node_id and r.node_id.startswith("doi:10.3390/mi15070851#") for r in refs)
    refs = enrich_from_jats(refs, (FIXTURES / "PMC11278924.xml").read_bytes())
    assert refs[0].ref_id == "B1-micromachines-15-00851" and refs[0].pmid == "36800415"
    assert refs[0].title is None  # MDPI's JATS keeps the entry as one citation string, with no <article-title>
    assert all(r.ref_id for r in refs) and sum(1 for r in refs if r.doi) >= 55


def test_numeric_markers_link_nodes_to_entries():
    tree = jats_tree()
    refs, cites = link_citations(tree, (FIXTURES / "PMC11278924.xml").read_bytes())
    by_node = {}
    for c in cites:
        by_node.setdefault(c.node_id, []).append(c.ref_no)
    # "[6,9,12]" in 2.2, "[1,2]" and "[3,4,5]" in the first paragraph of the introduction
    intro = next(n for n in tree.walk() if n.type == "paragraph" and n.text.startswith("Collagen is the primary structural protein"))
    assert by_node[intro.node_id][:5] == [1, 2, 3, 4, 5]
    elac = next(n for n in tree.walk() if n.type == "paragraph" and n.text.startswith("ELAC threads were prepared"))
    assert by_node[elac.node_id] == [6, 9, 12]
    assert all(1 <= c.ref_no <= 61 for c in cites)
    s = summarize(refs, cites)
    # the XML's <xref ref-type="bibr"> make 71 (paragraph, entry) pairs in 8 paragraphs; the tree's
    # paragraphs are finer, and one entry is named twice across a split
    assert s["refs"] == 61 and s["cited_refs"] == 61 and 68 <= s["citations"] <= 76 and 10 <= s["citing_nodes"] <= 20
    # an entry never cites itself, and nothing in the references lane cites
    assert not any(n.role == "references" for n in tree.walk() if n.node_id in by_node)


def author_year_doc():
    texts = [
        ("section_header", "1 Cognition all the way down"),
        ("text", "Cognition has been argued to reach below neurons (Lyon, 2020 , p. 41; Fields and Levin 2022) and, as Levin (2019, 2022a) puts it, all the way down. Others disagree (Adams & Garrison, 2013; Levin, 2022)."),
        ("text", "Amoebae navigate problem spaces (Fields et al., 2022 , Friston et al., 2023) with no brain [1]."),
        ("section_header", "References"),
        ("list_item", "Adams, F., & Garrison, R. (2013). The mark of the cognitive. Minds and Machines, 23(3), 339-352."),
        ("list_item", "Fields, C., & Levin, M. (2022). Competency in navigating arbitrary spaces. Entropy, 24(6), 819."),
        ("list_item", "Fields, C., Friston, K., Glazebrook, J. F., & Levin, M. (2022). A free energy principle for generic quantum systems. Progress in Biophysics, 173, 36-59."),
        ("list_item", "Friston, K., Da Costa, L., & Parr, T. (2023). Path integrals, particular kinds. Physics of Life Reviews, 47, 35-62."),
        ("list_item", "Levin, M. (2019). The computational boundary of a self. Frontiers in Psychology, 10, 2688."),
        ("list_item", "Levin, M. (2022a). Technological approach to mind everywhere. Frontiers in Systems Neuroscience, 16, 768201."),
        ("list_item", "Levin, M. (2022b). Collective intelligence of morphogenesis. Cellular and Molecular Life Sciences, 80, 142."),
        ("list_item", "Lyon, P. (2020). Of what is 'minimal cognition' the half-baked version? Adaptive Behavior, 28(6), 407-424."),
    ]
    items = []
    for i, (label, text) in enumerate(texts):
        items.append({"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "prov": [], "level": 1 if label == "section_header" else None})
    return {"name": "A made-up paper", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items, "pictures": [], "tables": [], "groups": [], "pages": {}}


def test_author_year_citations():
    tree = build_tree(author_year_doc(), "k")
    refs = reference_entries(tree)
    assert [(r.first_author, r.year) for r in refs] == [("Adams", "2013"), ("Fields", "2022"), ("Fields", "2022"), ("Friston", "2023"), ("Levin", "2019"), ("Levin", "2022a"), ("Levin", "2022b"), ("Lyon", "2020")]
    cites = find_citations(tree, refs)
    paras = [n for n in tree.walk() if n.type == "paragraph"]
    first = sorted(c.ref_no for c in cites if c.node_id == paras[0].node_id)
    # Lyon 2020 → 8; Fields and Levin 2022 → both Fields 2022 entries (2 and 3: the year alone cannot tell them apart);
    # Levin 2019 → 5; Levin 2022a → 6; Adams & Garrison 2013 → 1; Levin 2022 → 6 and 7
    assert first == [1, 2, 3, 5, 6, 7, 8]
    second = sorted(c.ref_no for c in cites if c.node_id == paras[1].node_id)
    assert second == [1, 2, 3, 4]  # "[1]" is a numeric marker too; Fields et al. 2022 → 2 and 3; Friston et al. 2023 → 4
    markers = {c.marker for c in cites if c.node_id == paras[0].node_id}
    assert "Lyon, 2020" in markers and "Levin (2019, 2022a)" in markers


def test_refs_and_citations_are_rows(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    key = "doi:10.3390/mi15070851"
    file_paper(conn, title="t", file="x.xml", sha256="abc", fmt="jats", doi="10.3390/mi15070851", pmid=None, pmcid=None, now="2026-09-11T00:00:00Z")
    tree = jats_tree()
    save_tree(conn, key, tree, parser="test", parsed_at="2026-09-11T00:00:00Z", seconds=0.0)
    refs, cites = link_citations(tree, (FIXTURES / "PMC11278924.xml").read_bytes())
    assert save_refs(conn, key, refs, cites) == {"refs": 61, "citations": len(cites)}
    rows = refs_of(conn, key)
    assert len(rows) == 61 and rows[5]["ref_no"] == 6 and rows[5]["cited_by"]
    elac = next(n for n in tree.walk() if n.type == "paragraph" and n.text.startswith("ELAC threads were prepared"))
    assert [c["ref_no"] for c in cites_of(conn, elac.node_id)] == [6, 9, 12]
    assert cites_of(conn, elac.node_id)[0]["doi"] and cites_of(conn, elac.node_id)[0]["marker"] == "[6,9,12]"
    citing = cited_by(conn, key, 6)
    assert elac.node_id in [c["node_id"] for c in citing] and all(c["role"] != "references" for c in citing)
    # the tree over the wire carries each node's cites and each entry's number
    t = paper_tree(conn, key)
    flat = []
    stack = [t["root"]]
    while stack:
        n = stack.pop()
        flat.append(n)
        stack.extend(n["children"])
    by_id = {n["node_id"]: n for n in flat}
    assert by_id[elac.node_id]["cites"] == [6, 9, 12] and by_id[refs[5].node_id]["ref_no"] == 6
    # saving again replaces, never doubles
    save_refs(conn, key, refs, cites)
    assert conn.execute("SELECT COUNT(*) FROM citations WHERE paper = ?", (key,)).fetchone()[0] == len(cites)


def test_superscript_citations_glued_to_words():
    texts = [("section_header", "1. Introduction")]
    texts.append(("text", "Damage to tendons is the most common soft tissue injury.1 Worldwide, more than half involve tendons and ligaments.2,3 Athletes suffer most,4-6 at 0.35/µm3 with CD34 cells."))
    texts.append(("text", "Healing is slow.7 Repair fails in 30% of cases, as reviewed elsewhere.1,8 Velocity was 77.6 ± 86.4 μm at 3.1 mg/mL (Purecol, 3.1 mg/mL)."))
    texts.append(("section_header", "References"))
    for i in range(1, 9):
        texts.append(("list_item", f"Author{i} A., Other B. A paper number {i}. Journal. {2000 + i};1:1-2."))
    items = [{"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": l, "text": t, "prov": [], "level": 1 if l == "section_header" else None} for i, (l, t) in enumerate(texts)]
    doc = {"name": "sup", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items, "pictures": [], "tables": [], "groups": [], "pages": {}}
    tree = build_tree(doc, "k")
    refs, cites = link_citations(tree)
    paras = [n for n in tree.walk() if n.type == "paragraph"]
    first = sorted(c.ref_no for c in cites if c.node_id == paras[0].node_id)
    assert first == [1, 2, 3, 4, 5, 6]  # not 3 from "µm3", not 34 from "CD34"
    assert sorted(c.ref_no for c in cites if c.node_id == paras[1].node_id) == [1, 7, 8]  # not 6 and 4 from "86.4", not 1 from "3.1"
    # one bracketed marker anywhere and the glued reading is off: the paper's style is brackets
    items[1]["text"] = items[1]["text"] + " See also [2]."
    tree = build_tree(doc, "k")
    refs, cites = link_citations(tree)
    assert sorted(c.ref_no for c in cites if c.node_id == paras[0].node_id) == [2]


def paper(texts, key="k"):
    """A document of (label, text) pairs, the way Docling hands one over."""
    items = [{"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": l, "text": t, "prov": [], "level": 1 if l == "section_header" else None} for i, (l, t) in enumerate(texts)]
    return {"name": "made up", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items, "pictures": [], "tables": [], "groups": [], "pages": {}}


def entries(n, first=1):
    return [("list_item", f"{i}. Author{i} A., Other B. A paper number {i}. Journal. {2000 + i % 20};1:1-2.") for i in range(first, first + n)]


def cited(texts, n_entries=40):
    tree = build_tree(paper(texts + [("section_header", "References")] + entries(n_entries)), "k")
    refs, cites = link_citations(tree)
    paras = [n for n in tree.walk() if n.type == "paragraph"]
    return tree, refs, cites, paras


def test_a_caret_marks_the_superscript_the_page_printed():
    # Scientific Reports, doi:10.1038/s41598-026-46107-7 and doi:10.1038/s41598-026-52941-6: the
    # layout model hands a raised number over with a caret, one caret to each number of a list
    tree, refs, cites, paras = cited([
        ("section_header", "Introduction"),
        ("text", "Tin oxide can be synthesized and deposited using various methods, including solution processing (e.g. spin-coating, spray pyrolysis),^21,^22 atomic layer deposition (ALD),^23 and magnetron sputtering^24. Among these, solution-processed SnO2 is widely favored for its low-temperature fabrication, affordability, and compatibility with flexible substrates.^21,^25-27"),
        ("text", "Internal waves propagate along density gradients and are induced by strong tidal flows interacting with bottom topography^7-11. Recent reef studies have underscored the importance of temperature variability^10,^37-39."),
    ])
    assert sorted(c.ref_no for c in cites if c.node_id == paras[0].node_id) == [21, 22, 23, 24, 25, 26, 27]
    assert sorted(c.ref_no for c in cites if c.node_id == paras[1].node_id) == [7, 8, 9, 10, 11, 37, 38, 39]


def test_a_caret_range_set_with_a_minus_sign():
    # ACS, doi:10.1021/acs.langmuir.6c03373: "^3−9" — the dash is U+2212, the minus sign, and a
    # range written with one is a range all the same
    tree, refs, cites, paras = cited([
        ("section_header", "Introduction"),
        ("text", "Atmospheric water harvesting produces freshwater using low-grade thermal or solar energy.^3\u22129 Sorbents for AWH should exhibit a large working capacity.^10,^11 Metal-organic frameworks are tunable.^13"),
    ])
    assert sorted(c.ref_no for c in cites if c.node_id == paras[0].node_id) == [3, 4, 5, 6, 7, 8, 9, 10, 11, 13]


def test_a_formula_is_not_a_superscript_where_the_carets_say_where_they_are():
    # ACS Omega, doi:10.1021/acsomega.6c00523: "BaTiO3", "SrTiO3" and "SiO2" were read as citations
    # 3, 3 and 2, a hundred of them in one paper. The carets say which numbers the page raised.
    tree, refs, cites, paras = cited([
        ("section_header", "Introduction"),
        ("text", "Perovskite oxides such as BaTiO3 and SrTiO3 have been studied for photocatalysis.^4 Composites with SiO2 improve the surface area.^5 The bandgap of BaTiO3 is wider than that of SrTiO3.^6"),
    ])
    assert sorted(c.ref_no for c in cites if c.node_id == paras[0].node_id) == [4, 5, 6]  # not 2 from "SiO2", not 3 from "BaTiO3"


def test_the_front_matter_is_not_read_for_markers():
    # doi:10.1038/s41598-026-52941-6: the author list and the addresses carry raised numbers of
    # their own, and none of them names an entry
    doc = paper([
        ("text", "Coral bleaching and the reefs that escape it"),
        ("text", "Hana Camelia^1, Thomas Felis^1, Jessica A. Hargreaves^1, Sander Scheffers^2, Marlene Wall^5"),
        ("text", "^1MARUM - Center for Marine Environmental Sciences, University of Bremen, 28359 Bremen, Germany.^2Oceans Institute, The University of Western Australia, Perth 6009, Australia."),
        ("section_header", "Introduction"),
        ("text", "Not all coral reefs respond to marine heatwaves similarly as certain locations can provide refugia from rising temperatures.^7 These processes are caused by the upward movement of thermal layers.^8,^9"),
    ] + [("section_header", "References")] + entries(40))
    tree = build_tree(doc, "k")
    refs, cites = link_citations(tree)
    assert sorted({c.ref_no for c in cites}) == [7, 8, 9]  # 1, 2 and 5 are affiliations


def test_a_bracket_around_every_entry_is_one_marker():
    # the same paper as JATS, where each <xref> is bracketed on its own: "[14]-[17]" is a range and
    # "[12],14,21,[40]" a list, and reading only what sits inside brackets loses the rest
    tree, refs, cites, paras = cited([
        ("section_header", "Introduction"),
        ("text", "An ideal EEL should combine high electron mobility and good energy level alignment [14]-[17]. Upwelling-influenced reefs were shown to experience reduced thermal stress, rendering healthier corals [12],14,21,[40]."),
    ])
    assert sorted(c.ref_no for c in cites if c.node_id == paras[0].node_id) == [12, 14, 15, 16, 17, 21, 40]


def test_an_en_dash_that_came_out_as_the_letter_e():
    # Journal of Orthopaedic Translation, doi:10.1016/j.jot.2017.02.005: Elsevier's en dash reaches
    # the text layer as "e", so "[1 e 3]" is "[1-3]"
    tree, refs, cites, paras = cited([
        ("section_header", "Introduction"),
        ("text", "Treatment is by anti-inflammatory drug injection, physical therapy, or surgery [1 e 3]. Proteoglycans accumulate in tendons and impair their mechanical properties [12 e 14]."),
    ])
    assert sorted(c.ref_no for c in cites if c.node_id == paras[0].node_id) == [1, 2, 3, 12, 13, 14]


def test_a_spaced_superscript_range_keeps_its_spaces():
    # doi:10.1021/acs.langmuir.6c03373 again, where the raised numbers arrive spaced out:
    # "requirements. 10 - 12 In contrast"
    tree, refs, cites, paras = cited([
        ("section_header", "Introduction"),
        ("text", "Conventional sorbents suffer from low capacities or high regeneration energy requirements. 10 - 12 In contrast, metal-organic frameworks are tunable. 13,14 Their uptake is high. 15 The isotherm is S-shaped. 16"),
    ])
    assert sorted(c.ref_no for c in cites if c.node_id == paras[0].node_id) == [10, 11, 12, 13, 14, 15, 16]


def test_two_reference_entries_the_layout_model_ran_together():
    # Frontiers, doi:10.3389/fepid.2026.1813211: entries 3 and 4 reached the tree as one block
    run_on = ("paragraph", "3. World Health Organisation. Avian influenza A(H5N1) virus, Human-animal interface, Global Influenza Programme (2024) Available online at: https://www.who.int/teams/global-influenza-programme/avian-influenza (Accessed May 8, 2026). 4. Food and Agriculture Organization. Global Avian Influenza Viruses with Zoonotic Potential situation update (2025).")
    doc = paper([("section_header", "Introduction"), ("text", "Human cases remain limited [1,2]. Poultry is the reservoir [3,4]."), ("section_header", "References")] + entries(2) + [run_on] + entries(2, first=5))
    tree = build_tree(doc, "k")
    refs, cites = link_citations(tree)
    assert [r.ref_no for r in refs] == [1, 2, 3, 4, 5, 6]
    assert refs[2].text.startswith("World Health Organisation")
    assert refs[3].text.startswith("Food and Agriculture")
    assert sorted(c.ref_no for c in cites) == [1, 2, 3, 4]
    assert tree.repairs.get("split_references") == 1
    assert {r.node_id for r in refs[2:4]} == {refs[2].node_id}  # both halves name the block they were cut from
    link_citations(tree)  # the worker links, then the confidence score links again: the count is set, not added to
    assert tree.repairs.get("split_references") == 1


def test_a_page_number_does_not_start_a_reference_entry():
    # doi:10.3390/mi14081643: "Biomedical Microdevices 2017, 19, 72. [CrossRef]" — 72 is the page,
    # and cutting there made a 179th entry out of 178 and shifted every number after it
    doc = paper([("section_header", "Introduction"), ("text", "Peristaltic pumping is one route [71]."), ("section_header", "References")]
                + entries(70)
                + [("list_item", "71. Shutko, A.V.; Gorbunov, V.S.; Guria, K.G.; Agladze, K.I. Biocontractile microfluidic channels for peristaltic pumping. Biomedical Microdevices 2017 , 19 , 72. [CrossRef]")]
                + entries(2, first=72))
    tree = build_tree(doc, "k")
    refs, _ = link_citations(tree)
    assert [r.ref_no for r in refs][-3:] == [71, 72, 73] and len(refs) == 73
    assert not tree.repairs.get("split_references")


def test_a_figures_number_is_not_a_superscript():
    # doi:10.1038/s41598-026-52941-6: a caption opens "Fig. 1. Map and climatology of the Andaman
    # Sea", and the glued reading took the 1 for a raised number
    tree, refs, cites, paras = cited([
        ("section_header", "Results"),
        ("text", "Coral bleaching followed the heatwave.^3 Reefs at depth were spared.^4,^5 The record runs from 1985.^6"),
        ("caption", "Fig. 1. Map and climatology of the Andaman Sea and Bay of Bengal, northeastern Indian Ocean."),
        ("caption", "Table 1. Ordinary least squares regression equations and Pearson correlation coefficients."),
    ])
    assert sorted({c.ref_no for c in cites}) == [3, 4, 5, 6]


def test_a_statistics_degrees_of_freedom_are_not_a_citation():
    # doi:10.3389/fnhum.2026.1832731, a Frontiers paper in the parenthetical style: "F (1, 13) =
    # 0.024, p = 0.88" put citations 1 and 13 in every sentence that reported a test
    tree, refs, cites, paras = cited([
        ("section_header", "Results"),
        ("text", "Sleep deprivation slowed responses (1, 2) and the caffeine dose did not (3, 4). The interaction between sleep condition and cognitive enhancer was not significant ( F (1, 13) = 0.024, p = 0.88). Accuracy was unaffected (5-7)."),
    ])
    assert sorted({c.ref_no for c in cites}) == [1, 2, 3, 4, 5, 6, 7]  # 13 is a degree of freedom



def test_a_pdf_entry_s_doi_mended_and_its_pmid_read():
    from litrag_parser.citations import _PMID, entry_doi

    # what six publishers' PDFs printed (2026-10-04), the line breaks as the text layer gave them
    assert entry_doi("Cell. 2013; 155(7):1639-51. https://doi.org/10.1016/j.cell. 2013.11.029 PMID: 24332913") == "10.1016/j.cell.2013.11.029"
    assert entry_doi("Biomaterials. 21(23), 2347 - 2359. doi:10.1016/s0142-9612(00) 00102-2") == "10.1016/s0142-9612(00)00102-2"
    assert entry_doi("J Abbrev. doi:10.1234/abc. 2019;5:1-9.") == "10.1234/abc"  # a year after the full stop: the citation going on
    assert entry_doi("Smith J (doi:10.1038/nature12373).") == "10.1038/nature12373"
    assert entry_doi("No identifier at all. 2019.") is None
    assert [m.group(1) for m in _PMID.finditer("Nat. Rev. Neurol 2013, 9, 668. [PubMed: 24217518] · Cancer Res. PMID: 19470768")] == ["24217518", "19470768"]


# ---------------------------------------------------------------- a PDF's reference list, assembled from its pieces


def pdf_paper(texts, key="k"):
    """A PDF's document: (label, text, page) triples, or (label, text, page, extra fields)."""
    items = []
    for i, t in enumerate(texts):
        label, text, page = t[:3]
        items.append({"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "level": 1 if label == "section_header" else None,
                      "prov": [{"page_no": page, "bbox": {"l": 50, "t": 800 - (i % 30) * 25, "r": 300, "b": 790 - (i % 30) * 25, "coord_origin": "BOTTOMLEFT"}}], **(t[3] if len(t) > 3 else {})})
    pages = sorted({t[2] for t in texts})
    return build_tree({"name": "made up", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items, "pictures": [], "tables": [], "groups": [],
                       "pages": {str(p): {"page_no": p, "size": {"width": 600, "height": 850}} for p in pages}}, key)


BODY = [("section_header", "Introduction", 1), ("text", "Vascular growth is driven by one family of factors [1], its receptors [2,3] and a co-receptor [4].", 1)]


def test_a_reviews_notes_under_its_entries_are_no_entries():
    """BMC's reviews print a note under an entry they recommend, and a legend above the list
    (doi:10.1186/gb-2005-6-2-209: 90 entries read where 61 were printed). The notes are the entry's;
    the printed numbers decide, so no note shifts the link of a citation after it."""
    tree = pdf_paper(BODY + [("section_header", "References", 2),
                             ("text", "Articles of particular interest have been highlighted as:", 2), ("text", "• of special interest", 2), ("text", "•• of outstanding interest", 2),
                             ("list_item", "1. Senger DR, Galli SJ, Dvorak AM: Tumor cells secrete a vascular permeability factor. Science 1983, 219:983-985.", 2),
                             ("text", "•• The initial discovery of a secreted VPF with the characteristics of VEGF-A.", 2),
                             ("list_item", "2. Ferrara N, Henzel WJ: Pituitary follicular cells secrete a novel heparin-binding growth factor. Biochem Biophys Res Commun 1989, 161:851-858.", 2),
                             ("text", "This and [4] are the first reports of the cDNA cloning of VEGF-A.", 2),
                             ("list_item", "3. Ogawa S, Oku A, Sawano A: A novel type of vascular endothelial growth factor, VEGF-E. J Biol Chem 1998, 273:31273-31282.", 2),
                             ("text", "A review of VEGF-E.", 2),
                             ("list_item", "4. Leung DW, Cachianes G, Kuang WJ: Vascular endothelial growth factor is a secreted angiogenic mitogen. Science 1989, 246:1306-1309.", 2)]
                     + [("list_item", f"{i}. Author{i} AB, Other CD: A paper on the receptors number {i}. J Biol Chem {1990 + i}, {i}:1-9.", 2) for i in range(5, 13)])
    refs, cites = link_citations(tree)
    assert [r.ref_no for r in refs] == list(range(1, 13))
    assert refs[0].text.endswith("with the characteristics of VEGF-A.") and refs[2].text.endswith("A review of VEGF-E.")
    assert sorted(c.ref_no for c in cites) == [1, 2, 3, 4]
    assert tree.repairs["reference_continuations"] == 3


def test_an_entry_a_column_break_cut_in_two_is_one_entry():
    """Frontiers and JCB lists set by author and year: the rest of an entry the column broke off
    reached the tree as an entry of its own ("(CaP) whisker-reinforced …", "tricalcium phosphate …").
    As list items, which the tree's own joining leaves apart, as the layout model gave these."""
    tree = pdf_paper([("section_header", "Introduction", 1), ("text", "Whiskers stiffen the composite (Choi et al., 2010), and silica dopes the ceramic (Fielding et al., 2012).", 1),
                      ("section_header", "References", 2),
                      ("list_item", "Choi, W.-Y., Kim, H.-E., Kim, M.-J., Kim, U.-C., Kim, J.-H., and Koh, Y.-H. (2010). Production and characterization of calcium phosphate", 2),
                      ("list_item", "(CaP) whisker-reinforced poly(ε-caprolactone) composites as bone regenerative. Mater. Sci. Eng. 30, 1280-1286.", 3),
                      ("list_item", "Fielding, G. A., Bandyopadhyay, A., and Bose, S. (2012). Effects of silica and zinc oxide doping on mechanical and biological properties of 3D printed", 3),
                      ("list_item", "tricalcium phosphate tissue engineering scaffolds. Dent. Mater. 28, 113-122.", 3),
                      ("list_item", "World Health Organization (2018). The Top 10 Causes of Death. Available online at: https://www.who.int", 3)])
    refs, cites = link_citations(tree)
    assert len(refs) == 3 and refs[0].text.endswith("Mater. Sci. Eng. 30, 1280-1286.") and refs[1].text.endswith("Dent. Mater. 28, 113-122.")
    assert refs[2].text.startswith("World Health Organization")
    assert sorted(c.ref_no for c in cites) == [1, 2]


def test_two_author_year_entries_run_together_are_cut_apart():
    """Frontiers' two columns read across: one entry's first line, then the next entry whole."""
    tree = pdf_paper([("section_header", "Introduction", 1), ("text", "Ameloblastoma alters bone nodules (Bakkalci et al., 2021).", 1), ("section_header", "References", 2),
                      ("text", "Astaneh, M. E., Noori, F., and Fereydouni, N. (2024). Curcumin-loaded scaffolds in Bakkalci, D., Jay, A., Rezaei, A., Howard, C. A., Haugen, H. J., Pape, J., et al. (2021). Bioengineering the ameloblastoma tumour to study its effect on bone nodule formation.", 2),
                      ("text", "Senthil, R., Sinem, Ç., Kavukcu, S. B., and Aruni, A. W. (2022). Fabrication of cylindrical bone graft substitute supported by reduced graphene-oxide. Mater. Lett. 322, 132475.", 2)])
    refs, cites = link_citations(tree)
    assert [r.text[:12] for r in refs] == ["Astaneh, M. ", "Bakkalci, D.", "Senthil, R.,"]  # a long list of authors is never cut
    assert [c.ref_no for c in cites] == [2] and tree.repairs["split_references"] == 1


def test_statements_set_in_the_reference_list_are_no_entries():
    """Frontiers sets its funding's last line, the generative-AI statement and the publisher's note
    among the entries; MDPI its disclaimer after the last (doi:10.3389/fbioe.2024.1505102,
    doi:10.3390/mi15070851). They are back matter, not entries."""
    tree = pdf_paper(BODY + [("section_header", "References", 2),
                             ("text", "Astaneh, M. E., Noori, F., and Fereydouni, N. (2024). Curcumin-loaded scaffolds in bone regeneration. Heliyon 10, e32566.", 2),
                             ("text", "Development Program of Heilongjiang Province (Grant No.2022ZX06C05).", 2),
                             ("section_header", "Generative AI statement", 2), ("text", "The author(s) declare that no Generative AI was used in the creation of this manuscript.", 2),
                             ("section_header", "Publisher’s note", 2), ("text", "All claims expressed in this article are solely those of the authors and do not necessarily represent those of their affiliated organizations.", 2),
                             ("text", "Bose, S., and Sarkar, N. (2020). Natural medicinal compounds in bone tissue engineering. Trends Biotechnol. 38, 404-417.", 2)])
    refs, _ = link_citations(tree)
    assert [r.first_author for r in refs] == ["Astaneh", "Bose"]
    roles = {n.heading: n.role for n in tree.walk() if n.type == "section"}
    assert roles["Generative AI statement"] == "back" and roles["Publisher’s note"] == "back"


def test_a_preprints_list_after_its_appendix_under_no_heading():
    """A physics preprint prints its numbered entries straight after the appendix, under no heading,
    their numbers in Docling's list marker and one pair read out of turn (doi:10.1103/physreve.68.061907
    was read with no references at all)."""
    entries = [(i, f"{'ABCDEFGHJKLMNP'[i % 14]}.{'QRSTUVWXYZ'[i % 10]}. Author{i} and K. Other, Phys. Rev. E {50 + i}, {1000 + i} ({1990 + i % 13}).") for i in range(1, 13)]
    entries[7], entries[8] = entries[8], entries[7]
    tree = pdf_paper([("section_header", "I. INTRODUCTION", 1), ("text", "Rigidity percolates at a threshold [3], and thermal effects soften it [9].", 1),
                      ("section_header", "APPENDIX", 2), ("text", "The affine limit follows from the energy of a single filament under strain.", 2)]
                     + [("list_item", text, 3, {"marker": f"[{i}]", "enumerated": True}) for i, text in entries])
    refs, cites = link_citations(tree)
    assert tree.repairs.get("inferred_references") and len(refs) == 12
    assert sorted(c.ref_no for c in cites) == [3, 9] and all(c.ref_no == next(r.ref_no for r in refs if r.text.startswith(f"{'ABCDEFGHJKLMNP'[c.ref_no % 14]}.")) for c in cites)


def test_numbers_printed_out_of_turn_still_number_the_list():
    texts = [f"[{i}] A. Author{i} and B. Other, J. Chem. Phys. {i}, {100 + i} ({1990 + i})." for i in range(1, 11)]
    texts[4], texts[5] = texts[5], texts[4]
    tree = pdf_paper(BODY + [("section_header", "References", 2)] + [("list_item", t, 2) for t in texts])
    refs, cites = link_citations(tree)
    assert [r.ref_no for r in refs][:7] == [1, 2, 3, 4, 6, 5, 7] and refs[4].text.startswith("A. Author6")
    assert sorted(c.ref_no for c in cites) == [1, 2, 3, 4]


def test_figure_legends_after_the_list_are_no_part_of_it():
    """An author manuscript sets one figure to a page after its references, each with its legend
    (doi:10.1242/dev.105.2.223 from OSTI: 15% of its prose read as the reference list)."""
    legend = ("Development of lumen-containing structures by cells cultured on the matrix. Isolated primary mammary epithelial cells were "
              "cultured on the matrix and photographed after eight days; the structures are polarized and secrete into a central lumen, "
              "as the phase-contrast view shows, and the bar marks fifty micrometres in every panel.")
    tree = pdf_paper(BODY + [("section_header", "References", 2),
                             ("text", "Hynes, R. O. (1987). Integrins: a family of cell surface receptors. Cell 48, 549-554.", 2),
                             ("text", "Williams, J. M. and Daniel, C. W. (1983). Mammary ductal elongation. Devl Biol. 97, 274-290.", 2),
                             ("text", legend, 3), ("text", legend.replace("Development", "Prevalence"), 4)])
    refs, _ = link_citations(tree)
    assert len(refs) == 2
    after = [n for n in tree.root.children if n.heading == "(untitled section)"]
    assert len(after) == 1 and after[0].role == "other" and len(after[0].children) == 2
    assert tree.repairs["after_references"] == 1


def test_a_list_in_a_style_no_rule_knows_keeps_every_piece():
    texts = ["Optical tweezers in cell biology, a review of the field, in: Methods in Cell Biology 1998.",
             "the forces of single motors, as measured in vitro, ibid. 1999.", "kinesin under load, ibid. 2001."]
    tree = pdf_paper(BODY + [("section_header", "References", 2)] + [("text", t, 2) for t in texts])
    refs, _ = link_citations(tree)
    assert [r.text for r in refs] == texts
