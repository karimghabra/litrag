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
