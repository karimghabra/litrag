"""Edges inside one paper: a finding to the method that produced it, by a pointer, by terms
only one method owns, by the caption of the figure it cites, or by resemblance last; a
finding no evidence can place stays unlinked; every edge a row that replays."""

from litrag_parser import lanes
from litrag_parser.edges import Edge, link_edges, method_candidates, summarize
from litrag_parser.store import edges_of, open_store, save_edges, save_tree
from litrag_parser.tree import build_tree

from test_structure import _doc

M_FAB = "Collagen scaffolds were cast from a 10 mg/mL solution and crosslinked with genipin for 24 h at room temperature, then rinsed in phosphate buffered saline before use."
M_MECH = "Compressive modulus was measured on an Instron 5944 at 1 mm/min to 30 % strain; the stress–strain curves were fitted between 5 and 15 % strain to give the modulus."
M_SWELL = "Swelling ratio was determined gravimetrically after immersion in water for 24 h as the wet mass over the dry mass."
M_LIVE = "Cell viability was assessed with calcein AM and ethidium homodimer (live/dead staining) after 1, 3 and 7 days, imaged on a confocal microscope."
R_MOD = "The compressive modulus rose from 12 ± 3 kPa to 48 ± 6 kPa as genipin concentration increased (Figure 3a), while the swelling ratio fell from 18 to 9 (Table 1)."
R_LIVE = "More than 90 % of the cells were alive on every scaffold at day 7 according to the live/dead staining (Figure 4)."
R_POINT = "The modulus of the crosslinked scaffolds was three times that of the controls (see Section 2.2), a difference that held at every strain examined here."
R_VAGUE = "The treated samples performed better than the controls in every respect that was examined over the course of the experiment."
R_FIG = "Taken together the scaffolds behaved as expected of a crosslinked network (Figure 4) in the hands of every operator who tried them."


def _paper(extra=()):
    texts = [
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "1 Introduction", 1), ("text", "Tendon injuries are common and heal slowly, which is why scaffolds have been studied for decades.", 1),
        ("section_header", "2 Materials and methods", 1),
        ("section_header", "2.1 Scaffold fabrication", 1), ("text", M_FAB, 1),
        ("section_header", "2.2 Mechanical testing", 1), ("text", M_MECH, 1),
        ("section_header", "2.3 Swelling", 1), ("text", M_SWELL, 1),
        ("section_header", "2.4 Cell viability", 1), ("text", M_LIVE, 1),
        ("section_header", "3 Results", 2), ("text", R_MOD, 2), ("text", R_LIVE, 2), ("text", R_POINT, 2), ("text", R_VAGUE, 2), ("text", R_FIG, 2),
        ("section_header", "4 Discussion", 2), ("text", "Our findings demonstrate that the crosslinked scaffolds support cell growth while providing mechanical properties in the range of native tendon.", 2),
    ]
    doc = _doc(texts + list(extra))
    for it in doc["texts"]:
        if it["label"] == "section_header" and it["text"][0].isdigit() and "." in it["text"].split()[0]:
            it["level"] = 2
    # two figures with captions, filed under the results
    for i, cap in enumerate(["Figure 3. Mechanical properties of the scaffolds: (a) compressive modulus, (b) stress–strain curves.", "Figure 4. Live/dead staining of cells on the scaffolds at day 7; green, calcein AM; red, ethidium homodimer."]):
        ref = f"#/texts/{len(doc['texts'])}"  # a caption is a text item, referred to by its index
        pic = {"self_ref": f"#/pictures/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": "picture", "captions": [{"$ref": ref}], "prov": [{"page_no": 2, "bbox": {"l": 50, "t": 300 - 100 * i, "r": 500, "b": 250 - 100 * i, "coord_origin": "BOTTOMLEFT"}}]}
        capt = {"self_ref": ref, "parent": {"$ref": f"#/pictures/{i}"}, "children": [], "label": "caption", "text": cap, "prov": [{"page_no": 2, "bbox": {"l": 50, "t": 248 - 100 * i, "r": 500, "b": 240 - 100 * i, "coord_origin": "BOTTOMLEFT"}}]}
        doc["pictures"].append(pic)
        doc["texts"].append(capt)
        # the figure sits after the results paragraphs
        idx = next(k for k, c in enumerate(doc["body"]["children"]) if c["$ref"] == "#/texts/18")
        doc["body"]["children"].insert(idx, {"$ref": f"#/pictures/{i}"})
    return doc


def _tree():
    return build_tree(_paper(), "k")


def _results_paper(extra):
    """`_paper` with extra paragraphs placed in the results, before the discussion heading."""
    doc = _paper(extra)
    moved = [doc["body"]["children"].pop() for _ in extra]
    at = next(k for k, c in enumerate(doc["body"]["children"]) if c["$ref"] == "#/texts/18")
    for c in reversed(moved):
        doc["body"]["children"].insert(at, c)
    return doc


def _by_text(tree, start):
    return next(n for n in tree.walk() if n.text.startswith(start))


def test_the_methods_subsections_are_the_candidates():
    tree = _tree()
    heads = [n.heading for n, _ in method_candidates(tree)]
    assert heads == ["2.1 Scaffold fabrication", "2.2 Mechanical testing", "2.3 Swelling", "2.4 Cell viability"]


def test_a_finding_is_linked_by_a_pointer_by_terms_by_its_figures_caption_or_not_at_all():
    tree = _tree()
    edges = link_edges(tree, "k")
    by = {}
    for e in edges:
        if e.kind == "measured_by":
            by.setdefault(e.src, []).append(e)
    mod, live, point, vague, fig = (_by_text(tree, s).node_id for s in ("The compressive modulus", "More than 90", "The modulus of the", "The treated samples", "Taken together"))
    sec = {n.heading: n.node_id for n in tree.walk() if n.type == "section"}
    # a pointer decides on its own
    assert [(e.dst, e.evidence, e.detail) for e in by[point]] == [(sec["2.2 Mechanical testing"], "pointer", "Section 2.2")]
    # terms only one method owns: a paragraph that reports a modulus and a swelling ratio rested on two methods
    assert {(e.dst, e.evidence) for e in by[mod]} == {(sec["2.2 Mechanical testing"], "terms"), (sec["2.3 Swelling"], "terms")}
    assert any("compressive modulus" in e.detail for e in by[mod])
    assert [(e.dst, e.evidence) for e in by[live]] == [(sec["2.4 Cell viability"], "terms")]
    # a terse finding reaches its method through the caption of the figure it cites
    assert [(e.dst, e.evidence) for e in by[fig]] == [(sec["2.4 Cell viability"], "caption")] and by[fig][0].detail.startswith("Figure 4")
    # nothing names a method for the vague one, and no oracle was given: unlinked
    assert vague not in by
    s = summarize(tree, edges)
    assert s["findings"] == 5 and s["linked"] == 4 and s["unlinked"] == 1 and s["pointer"] == 1 and s["terms"] == 3 and s["caption"] == 1 and s["candidates"] == 4
    # figures cited are edges too
    figs = {(e.src, e.detail) for e in edges if e.kind == "cites_figure"}
    assert (mod, "Figure 3") in figs and (mod, "Table 1") not in figs and (live, "Figure 4") in figs  # Table 1 has no node here


def test_similarity_is_the_last_resort_only_when_asked_for_and_replays_from_the_store(fake_oracle, monkeypatch):
    from litrag_parser import edges as E

    doc = _results_paper([("text", "Samples were loaded on the Instron fixture at a constant rate and the load was recorded for every group of the study.", 2)])
    tree = build_tree(doc, "k")
    loaded = _by_text(tree, "Samples were loaded").node_id
    assert not [e for e in link_edges(tree, "k", fake_oracle) if e.src == loaded]  # off by default
    monkeypatch.setenv("LITRAG_EDGES_SIMILARITY", "on")
    monkeypatch.setattr(E, "SIMILARITY_THRESHOLD", 0.3)  # the toy embedder is coarse; the plumbing is what is tested
    monkeypatch.setattr(E, "SIMILARITY_MARGIN", 0.02)
    edges = link_edges(tree, "k", fake_oracle)
    sim = [e for e in edges if e.src == loaded]
    mech = next(n.node_id for n in tree.walk() if n.heading == "2.2 Mechanical testing")
    assert [(e.dst, e.evidence) for e in sim] == [(mech, "similarity")] and sim[0].detail.startswith("cosine")
    # the verdict is a row: the same edges without the embedder
    fake_oracle._embed = lambda texts: (_ for _ in ()).throw(AssertionError("asked the embedder"))
    assert link_edges(tree, "k", fake_oracle) == edges
    # and only findings nothing else could place were put to the embedder
    vague = _by_text(tree, "The treated samples").node_id
    assert {e.src for e in edges if e.evidence == "similarity"} <= {loaded, vague}
    assert all(e.evidence != "similarity" for e in edges if e.src not in (loaded, vague))


def test_edges_are_rows_that_read_both_ways(tmp_path):
    tree = _tree()
    conn = open_store(tmp_path / "store.sqlite")
    conn.execute("INSERT INTO papers(key, title, added_at) VALUES ('k', 't', '2026-01-01T00:00:00Z')")
    conn.commit()
    save_tree(conn, "k", tree, parser="test", parsed_at="2026-01-01T00:00:00Z", seconds=0.0)
    edges = link_edges(tree, "k")
    assert save_edges(conn, "k", edges) == len(edges)
    assert save_edges(conn, "k", edges) == len(edges)  # again: the same rows, not twice
    assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == len(edges)
    mod = _by_text(tree, "The compressive modulus").node_id
    mech = next(n.node_id for n in tree.walk() if n.heading == "2.2 Mechanical testing")
    out = edges_of(conn, mod)["out"]
    assert {(e["kind"], e["evidence"], e["heading"]) for e in out} >= {("measured_by", "terms", "2.2 Mechanical testing"), ("measured_by", "terms", "2.3 Swelling")}
    inc = edges_of(conn, mech)["in"]
    assert {e["evidence"] for e in inc} == {"terms", "pointer"} and all(e["role"] == "results" for e in inc)


def test_a_paper_whose_methods_have_no_subsections_links_to_its_paragraphs():
    texts = [
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "2 Materials and methods", 1), ("text", M_MECH, 1), ("text", M_LIVE, 1),
        ("section_header", "3 Results", 2), ("text", R_MOD, 2), ("text", R_LIVE, 2),
    ]
    tree = build_tree(_doc(texts), "k")
    edges = [e for e in link_edges(tree, "k") if e.kind == "measured_by"]
    mod, live = _by_text(tree, "The compressive modulus").node_id, _by_text(tree, "More than 90").node_id
    m_mech, m_live = _by_text(tree, "Compressive modulus was").node_id, _by_text(tree, "Cell viability was").node_id
    assert {(e.src, e.dst, e.evidence) for e in edges} == {(mod, m_mech, "terms"), (live, m_live, "terms")}


def test_a_number_in_parentheses_is_not_a_pointer_and_a_list_of_sections_is_several():
    tree = build_tree(_results_paper([
        ("text", "The modulus was 48 kPa (2.4-fold higher than the controls) at 1.2 ± 0.3 mm/min in every group tested here.", 2),
        ("text", "Both assays were run as described in Sections 2.2 and 2.4 on every scaffold of the series.", 2),
    ]), "k")
    edges = [e for e in link_edges(tree, "k") if e.evidence == "pointer"]
    fold = _by_text(tree, "The modulus was 48").node_id
    both = _by_text(tree, "Both assays").node_id
    sec = {n.heading: n.node_id for n in tree.walk() if n.type == "section"}
    assert not [e for e in edges if e.src == fold]
    assert {e.dst for e in edges if e.src == both} == {sec["2.2 Mechanical testing"], sec["2.4 Cell viability"]}


def test_a_pair_the_whole_paper_uses_is_nobodys_mark():
    # "collagen scaffolds" said twice in one method and in five blocks of the paper names nothing
    extra = [("text", f"The collagen scaffolds held their shape in trial {n} and nothing else was observed in that trial.", 2) for n in range(4)]
    doc = _paper(extra)
    doc["texts"][5]["text"] = M_FAB + " The collagen scaffolds were stored dry; collagen scaffolds older than a week were discarded."
    tree = build_tree(doc, "k")
    fab = next(n.node_id for n in tree.walk() if n.heading == "2.1 Scaffold fabrication")
    assert not [e for e in link_edges(tree, "k") if e.dst == fab and e.evidence == "terms"]


def test_figures_cited_in_a_list_are_each_an_edge_once(tmp_path):
    tree = build_tree(_results_paper([("text", "The trends held across Figures 3 and 4 and again in Figure 3, as the panels show for every group.", 2)]), "k")
    para = _by_text(tree, "The trends held").node_id
    figs = sorted(e.detail for e in link_edges(tree, "k") if e.kind == "cites_figure" and e.src == para)
    assert figs == ["Figures 3", "Figures 4"]  # one edge per figure, the second mention of Figure 3 not a second edge
    conn = open_store(tmp_path / "store.sqlite")
    conn.execute("INSERT INTO papers(key, title, added_at) VALUES ('k', 't', '2026-01-01T00:00:00Z')")
    conn.commit()
    save_tree(conn, "k", tree, parser="test", parsed_at="2026-01-01T00:00:00Z", seconds=0.0)
    edges = link_edges(tree, "k")
    assert save_edges(conn, "k", edges) == len(edges)  # what was stored is what was counted
    fig4 = next(n.node_id for n in tree.walk() if n.type == "picture" and any(c.text.startswith("Figure 4") for c in n.children))
    assert {e["kind"] for e in edges_of(conn, fig4)["in"]} == {"cites_figure"} and edges_of(conn, fig4)["candidates"] == 4


def test_a_paper_without_methods_has_no_edges_and_says_so():
    texts = [("title", "A letter", 1), ("section_header", "Results", 1), ("text", R_MOD, 1)]
    tree = build_tree(_doc(texts), "k")
    edges = link_edges(tree, "k")
    assert not [e for e in edges if e.kind == "measured_by"]
    assert summarize(tree, edges)["candidates"] == 0
