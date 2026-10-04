"""The truth for the finding→method links: a queue of findings to label spread across papers
and publishers, labels that are rows a reread or a rebuild does not touch and that are found
again when the node ids move, the linker measured against them, and a label set carried out
of a library and into another."""

import json
import shutil

import pytest

from litrag_parser import truth
from litrag_parser.edges import findings, link_edges, method_candidates
from litrag_parser.store import file_paper, open_store, save_edges, save_tree
from litrag_parser.tree import build_tree

from test_edges import M_LIVE, M_MECH, R_LIVE, _paper, _results_paper
from test_structure import _doc
from test_worker import FIXTURES, by, talk

SWELL_F = "The swelling ratio of every crosslinked batch stayed below that of the untreated batches throughout the week."
ASIDE_F = "Handling the scaffolds with forceps left visible marks on the softer ones, which were photographed for the record."
STERILE = "Scaffolds were sterilised in 70 % ethanol for 30 min and rinsed three times in sterile water before any test."
OPENING = "All scaffolds kept their shape through every test, and none was excluded from the analyses reported below."


def _read(conn, key, doc):
    tree = build_tree(doc, key)
    save_tree(conn, key, tree, parser="test", parsed_at="t", seconds=0.0)
    save_edges(conn, key, link_edges(tree, key))
    return tree


def _file(conn, n, doi, doc):
    key = file_paper(conn, title=f"paper {n}", file=f"{n}.pdf", sha256=f"sha{n}", fmt="pdf", doi=doi, pmid=None, pmcid=None, now=f"2026-01-0{n}").key
    return key, _read(conn, key, doc)


PAPERS = [
    ("10.3390/mi15070851", lambda: json.loads((FIXTURES / "PMC11278924.docling.json").read_text())),
    ("10.1186/1476-4598-11-1", lambda: json.loads((FIXTURES / "PMC3258128.docling.json").read_text())),
    ("10.1016/j.test.2026.1", _paper),
]


@pytest.fixture
def library(tmp_path):
    """Three papers from three publishers, read and linked."""
    conn = open_store(tmp_path / "store.sqlite")
    trees = {}
    for n, (doi, doc) in enumerate(PAPERS, 1):
        key, tree = _file(conn, n, doi, doc())
        trees[key] = tree
    yield conn, trees
    conn.close()


def _node(tree, start):
    return next(n for n in tree.walk() if n.text.startswith(start))


def _section(tree, heading):
    return next(n for n in tree.walk() if n.type == "section" and n.heading == heading)


# -- the rows give what the tree gave ------------------------------------------------------------


def test_the_rows_give_the_candidates_and_findings_the_tree_gave(library):
    conn, trees = library
    for key, tree in trees.items():
        rows = truth.tree_of(conn, key)
        assert [(n.node_id, t) for n, t in method_candidates(rows)] == [(n.node_id, t) for n, t in method_candidates(tree)]
        assert [n.node_id for n in findings(rows)] == [n.node_id for n in findings(tree)]
    assert truth.tree_of(conn, "doi:nothing") is None
    assert truth.registrant("10.3390/mi15070851") == "10.3390" and truth.registrant("https://doi.org/10.1016/j.x.1") == "10.1016" and truth.registrant(None) == "no-doi"


# -- the queue ------------------------------------------------------------------------------------


def test_the_queue_spreads_across_publishers_and_papers_and_mixes_linked_with_unlinked(library):
    conn, trees = library
    q = truth.queue(conn, n=12, seed=7, per_paper=4)
    items = q["items"]
    assert len(items) == 12 and q["papers"] == 3 and q["labelled"] == 0
    papers = list(dict.fromkeys(i["paper"] for i in items))
    assert len({i["prefix"] for i in items if i["paper"] in papers[:3]}) == 3  # a paper from each publisher before a second from any
    assert all(sum(i["paper"] == p for i in items) <= 4 for p in papers)  # the cap per paper
    assert [i["paper"] for i in items] == sorted((i["paper"] for i in items), key=papers.index)  # a paper's findings together
    kinds = {i["evidence"] for i in items}
    assert "unlinked" in kinds and kinds & {"terms", "pointer", "caption"}  # linked and unlinked both
    for key in papers:  # within a paper, one kind of evidence after another, not every linked one first
        mine = [i["evidence"] for i in items if i["paper"] == key]
        if len(set(mine)) > 1:
            assert mine[0] != mine[1]
    for i in items:  # the candidates in method_candidates order, the finding's edges on them
        tree = trees[i["paper"]]
        assert [c["node_id"] for c in i["candidates"]] == [n.node_id for n, _ in method_candidates(tree)]
        edges = [e for e in link_edges(tree, i["paper"]) if e.kind == "measured_by" and e.src == i["finding"]["node_id"]]
        assert [(e["dst"], e["evidence"]) for e in i["edges"]] == [(e.dst, e.evidence) for e in edges]
        assert {c["node_id"]: c["edge"]["evidence"] for c in i["candidates"] if c["edge"]} == {e.dst: e.evidence for e in edges}
        assert i["evidence"] == (edges[0].evidence if edges else "unlinked")
        f = next(n for n in tree.walk() if n.node_id == i["finding"]["node_id"])
        assert (i["finding"]["text"], i["finding"]["ancestry"], i["finding"]["page"]) == (f.text, f.ancestry, f.page)
        sec = next(c for c in i["candidates"] if c["type"] == "section")
        assert sec["heading"] and sec["paragraphs"] and all(len(p["text"]) <= truth.TEXT_CHARS for p in sec["paragraphs"])
    synthetic = next(i for i in items if i["prefix"] == "10.1016")
    live = next(c for c in synthetic["candidates"] if c["heading"] == "2.4 Cell viability")
    assert [p["text"] for p in live["paragraphs"]] == [M_LIVE]


def test_the_queue_is_the_same_twice_skips_what_is_labelled_and_counts_it_against_the_cap(library):
    conn, _ = library
    first = truth.queue(conn, n=12, seed=7, per_paper=4)
    assert truth.queue(conn, n=12, seed=7, per_paper=4) == first
    assert any(truth.queue(conn, n=12, seed=s, per_paper=4)["items"] != first["items"] for s in (1, 2, 3))
    done = first["items"][0]
    truth.save_labels(conn, done["finding"]["node_id"], [{"verdict": "none"}], by="test")
    again = truth.queue(conn, n=12, seed=7, per_paper=4)
    ids = [i["finding"]["node_id"] for i in again["items"]]
    assert done["finding"]["node_id"] not in ids and again["labelled"] == 1
    same_paper = [i["finding"]["node_id"] for i in first["items"] if i["paper"] == done["paper"]][1:]
    assert [i for i in ids if i in same_paper or i.startswith(done["paper"] + "#")] == same_paper  # the rest of that paper, in the order it had: three, not four
    one = truth.queue(conn, finding=done["finding"]["node_id"])
    assert len(one["items"]) == 1 and one["items"][0]["labels"][0]["verdict"] == "none"
    with pytest.raises(ValueError):
        truth.queue(conn, finding="doi:nothing#section-1")


# -- labels ---------------------------------------------------------------------------------------


def test_labels_are_saved_replaced_and_read_back(library):
    conn, trees = library
    key = next(k for k in trees if k.startswith("doi:10.1016"))
    tree = trees[key]
    live, mod = _node(tree, "More than 90"), _node(tree, "The compressive modulus")
    via, mech, swell, fab = (_section(tree, h) for h in ("2.4 Cell viability", "2.2 Mechanical testing", "2.3 Swelling", "2.1 Scaffold fabrication"))
    para = _node(tree, M_LIVE[:30])
    saved = truth.save_labels(conn, live.node_id, [{"method": via.node_id, "verdict": "yes", "paragraph": para.node_id}, {"method": mech.node_id, "verdict": "no"}], by="Karim", at="2026-10-04T00:00:00Z")
    assert {(r["method"], r["verdict"], r["paragraph"]) for r in saved} == {(via.node_id, "yes", para.node_id), (mech.node_id, "no", None)}
    rows = {r["method"]: r for r in truth.labels(conn)}
    assert rows[via.node_id]["finding_text"] == R_LIVE[: truth.TEXT_CHARS] and rows[via.node_id]["method_heading"] == "2.4 Cell viability"
    assert rows[via.node_id]["paragraph_text"] == M_LIVE[: truth.TEXT_CHARS] and rows[via.node_id]["by"] == "Karim" and rows[via.node_id]["at"] == "2026-10-04T00:00:00Z"
    assert all(r["finding_now"] == live.node_id and r["method_now"] == r["method"] and r["how"] == "id" for r in rows.values())
    assert rows[via.node_id]["paragraph_now"] == para.node_id
    # saving again replaces the finding's set; another finding's labels stay
    truth.save_labels(conn, mod.node_id, [{"method": mech.node_id, "verdict": "yes"}, {"method": swell.node_id, "verdict": "yes"}, {"method": fab.node_id, "verdict": "no"}])
    truth.save_labels(conn, live.node_id, [{"verdict": "none"}])
    got = {(r["finding"], r["method"], r["verdict"]) for r in truth.labels(conn)}
    assert got == {(live.node_id, "", "none"), (mod.node_id, mech.node_id, "yes"), (mod.node_id, swell.node_id, "yes"), (mod.node_id, fab.node_id, "no")}
    truth.save_labels(conn, live.node_id, [])  # an empty set clears it
    assert {r["finding"] for r in truth.labels(conn)} == {mod.node_id}
    # what cannot be a label is refused, and nothing is written
    for bad in ([{"method": via.node_id, "verdict": "maybe"}], [{"method": "doi:other#section-1", "verdict": "yes"}],
                [{"verdict": "none"}, {"method": via.node_id, "verdict": "yes"}],
                [{"method": mech.node_id, "verdict": "yes", "paragraph": para.node_id}],  # not a paragraph of that method
                [{"method": via.node_id, "verdict": "no", "paragraph": para.node_id}]):
        with pytest.raises(ValueError):
            truth.save_labels(conn, live.node_id, bad)
    with pytest.raises(ValueError):
        truth.save_labels(conn, "doi:nothing#section-1", [{"verdict": "none"}])
    assert {r["finding"] for r in truth.labels(conn)} == {mod.node_id}


def _renumbered(drop_vague=False, retouch_live=False):
    """`_paper` read again with a subsection set before 2.2 and a paragraph before the first
    finding: every node after them has a new id."""
    items = [(t["label"], t["text"], t["prov"][0]["page_no"]) for t in _paper()["texts"] if t["label"] != "caption"]
    at = lambda s: next(k for k, it in enumerate(items) if it[1] == s)  # noqa: E731
    items[at("2.2 Mechanical testing"):at("2.2 Mechanical testing")] = [("section_header", "2.1a Sterilisation", 1), ("text", STERILE, 1)]
    items.insert(at("3 Results") + 1, ("text", OPENING, 2))
    if drop_vague:
        items = [it for it in items if not it[1].startswith("The treated samples")]
    if retouch_live:
        items[at(R_LIVE)] = ("text", R_LIVE.replace("90 %", "90%"), 2)
    doc = _doc(items)
    for it in doc["texts"]:  # as `_paper` does: "2.1 …" is a subsection
        if it["label"] == "section_header" and it["text"][0].isdigit() and "." in it["text"].split()[0]:
            it["level"] = 2
    return doc


def test_labels_survive_a_rebuild_that_renumbers_the_nodes(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    key, old = _file(conn, 1, "10.1016/j.test.2026.1", _paper())
    live, mod, vague = _node(old, "More than 90"), _node(old, "The compressive modulus"), _node(old, "The treated samples")
    via, mech, swell = (_section(old, h) for h in ("2.4 Cell viability", "2.2 Mechanical testing", "2.3 Swelling"))
    truth.save_labels(conn, live.node_id, [{"method": via.node_id, "verdict": "yes", "paragraph": _node(old, M_LIVE[:30]).node_id}])
    truth.save_labels(conn, mod.node_id, [{"method": mech.node_id, "verdict": "yes"}, {"method": swell.node_id, "verdict": "no"}])
    truth.save_labels(conn, vague.node_id, [{"verdict": "none"}])
    stored = conn.execute("SELECT * FROM link_labels ORDER BY finding, method").fetchall()

    new = _read(conn, key, _renumbered(drop_vague=True, retouch_live=True))  # what a rebuild does: the rows replaced
    assert [tuple(r) for r in conn.execute("SELECT * FROM link_labels ORDER BY finding, method")] == [tuple(r) for r in stored]  # the labels untouched
    assert _node(new, "More than 90").node_id != live.node_id and _section(new, "2.4 Cell viability").node_id != via.node_id
    assert next(n for n in new.walk() if n.node_id == via.node_id).heading == "2.3 Swelling"  # the old id now names another section
    now = {(r["finding"], r["method"]): r for r in truth.labels(conn)}
    r = now[(live.node_id, via.node_id)]
    assert (r["finding_now"], r["method_now"], r["paragraph_now"], r["how"]) == (_node(new, "More than 90").node_id, _section(new, "2.4 Cell viability").node_id, _node(new, M_LIVE[:30]).node_id, "resemblance")
    r = now[(mod.node_id, mech.node_id)]
    assert (r["finding_now"], r["method_now"], r["how"]) == (_node(new, "The compressive modulus").node_id, _section(new, "2.2 Mechanical testing").node_id, "text")
    assert now[(mod.node_id, swell.node_id)]["method_now"] == _section(new, "2.3 Swelling").node_id
    assert now[(vague.node_id, "")]["finding_now"] is None and now[(vague.node_id, "")]["how"] == "lost"  # gone from the paper: counted, not guessed
    m = truth.measure(conn)
    assert m["findings"] == 2 and m["unanchored"] == {"findings": 1, "methods": 0, "paragraphs": 0}
    assert m["paragraph"]["named"] == 1 and m["paragraph"]["right"] == 1
    # the queue knows the moved findings are labelled; labelling one again replaces its old rows
    assert not {_node(new, "More than 90").node_id, _node(new, "The compressive modulus").node_id} & {i["finding"]["node_id"] for i in truth.queue(conn, n=50)["items"]}
    truth.save_labels(conn, _node(new, "More than 90").node_id, [{"method": _section(new, "2.4 Cell viability").node_id, "verdict": "yes"}])
    assert not [r for r in truth.labels(conn) if r["finding"] == live.node_id]
    conn.close()


# -- the measure ----------------------------------------------------------------------------------


def test_measure_reports_every_number_on_a_hand_made_label_set(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    key, tree = _file(conn, 1, "10.1016/j.test.2026.1", _results_paper([("text", SWELL_F, 2), ("text", ASIDE_F, 2)]))
    f = {name: _node(tree, start).node_id for name, start in (("point", "The modulus of the"), ("mod", "The compressive modulus"), ("live", "More than 90"), ("swell", "The swelling ratio of every"), ("fig", "Taken together"), ("vague", "The treated samples"), ("aside", "Handling the scaffolds"))}
    s = {h.split()[0]: _section(tree, h).node_id for h in ("2.1 Scaffold fabrication", "2.2 Mechanical testing", "2.3 Swelling", "2.4 Cell viability")}
    edges = {(e.src, e.dst): e.evidence for e in link_edges(tree, key) if e.kind == "measured_by"}
    assert edges == {(f["point"], s["2.2"]): "pointer", (f["mod"], s["2.2"]): "terms", (f["mod"], s["2.3"]): "terms", (f["live"], s["2.4"]): "terms",
                     (f["swell"], s["2.3"]): "terms", (f["fig"], s["2.4"]): "caption"}  # the set-up: vague and aside unlinked

    def every(finding, yes, paragraph=None):
        return [{"method": m, "verdict": "yes" if k == yes else "no", **({"paragraph": paragraph} if k == yes and paragraph else {})} for k, m in s.items()]

    truth.save_labels(conn, f["point"], every(f["point"], "2.2", _node(tree, M_MECH[:30]).node_id))  # the pointer, right
    truth.save_labels(conn, f["mod"], every(f["mod"], "2.2"))  # terms: one edge right, one wrong
    truth.save_labels(conn, f["live"], [{"method": s["2.4"], "verdict": "yes", "paragraph": _node(tree, M_LIVE[:30]).node_id}])  # terms, right
    truth.save_labels(conn, f["swell"], [{"method": s["2.1"], "verdict": "yes"}])  # its edge to 2.3 has no label: unjudged; the edge went astray
    truth.save_labels(conn, f["fig"], [{"verdict": "none"}])  # the caption's edge is a false link
    truth.save_labels(conn, f["vague"], [{"method": s["2.2"], "verdict": "yes", "paragraph": _node(tree, M_MECH[:30]).node_id}])  # a miss
    truth.save_labels(conn, f["aside"], [{"method": m, "verdict": "no"} for m in s.values()])  # its method is not among the candidates
    with conn:  # labels no longer found: a finding gone, a method gone, a paragraph gone
        conn.execute("INSERT INTO link_labels(paper, finding, finding_text, method, method_heading, verdict, at) VALUES (?, ?, 'Words this paper never printed in any of its paragraphs.', ?, '2.2 Mechanical testing', 'yes', 't')", (key, f"{key}#section-3#paragraph-40", s["2.2"]))
        conn.execute("INSERT INTO link_labels(paper, finding, finding_text, method, method_heading, verdict, at) VALUES (?, ?, ?, ?, '2.9 Rheology', 'no', 't')", (key, f["aside"], ASIDE_F, f"{key}#section-2#section-9"))
        conn.execute("UPDATE link_labels SET paragraph = ?, paragraph_text = 'Nothing of the kind.' WHERE finding = ?", (f"{s['2.2']}#paragraph-9", f["vague"]))

    m = truth.measure(conn)
    assert m["labels"] == 18 and m["verdicts"] == {"yes": 6, "no": 11, "none": 1}
    assert m["findings"] == 7 and m["papers"] == 1
    assert m["unanchored"] == {"findings": 1, "methods": 1, "paragraphs": 1}
    p = m["precision"]
    assert (p["pointer"]["right"], p["pointer"]["edges"], p["pointer"]["precision"]) == (1, 1, 1.0)
    assert (p["terms"]["right"], p["terms"]["edges"], p["terms"]["precision"], p["terms"]["unjudged"]) == (2, 3, 0.667, 1)
    assert (p["caption"]["right"], p["caption"]["edges"], p["caption"]["precision"]) == (0, 1, 0.0)
    assert (p["similarity"]["edges"], p["similarity"]["precision"]) == (0, None)
    assert (p["all"]["right"], p["all"]["edges"], p["all"]["precision"], p["all"]["unjudged"]) == (3, 5, 0.6, 1)
    assert m["recall"] == {"methods": 5, "reached": 3, "recall": 0.6, "by": {"pointer": 1, "terms": 2, "caption": 0, "similarity": 0}}
    assert m["misses"]["count"] == 1 and m["misses"]["findings"][0]["finding"] == f["vague"] and m["misses"]["findings"][0]["methods"] == ["2.2 Mechanical testing"]
    assert m["false_links"]["count"] == 1 and m["false_links"]["findings"][0]["edges"] == [{"method": "2.4 Cell viability", "evidence": "caption", "detail": next(e.detail for e in link_edges(tree, key) if e.src == f["fig"] and e.kind == "measured_by")}]
    assert m["astray"] == 1 and m["outside"] == 1 and m["none"] == 1
    assert m["paragraph"] == {"named": 2, "right": 2, "accuracy": 1.0, "chooser": "first_paragraph"}

    def last_paragraph(c, finding, method):
        return None

    assert truth.measure(conn, choose_paragraph=last_paragraph)["paragraph"] == {"named": 2, "right": 0, "accuracy": 0.0, "chooser": "last_paragraph"}
    seen = []
    truth.measure(conn, choose_paragraph=lambda c, finding, method: seen.append((finding, method)))
    assert sorted(seen) == sorted([(f["point"], s["2.2"]), (f["live"], s["2.4"])])
    assert truth.first_paragraph(conn, f["live"], s["2.4"]) == _node(tree, M_LIVE[:30]).node_id
    conn.close()


def test_measure_with_no_labels_says_nothing_rather_than_zero(library):
    conn, _ = library
    m = truth.measure(conn)
    assert m["labels"] == 0 and m["precision"]["all"]["precision"] is None and m["recall"]["recall"] is None and m["paragraph"]["accuracy"] is None


# -- a label set outside the library ---------------------------------------------------------------


def test_export_and_import_round_trip_and_find_the_labels_again_elsewhere(tmp_path, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    ca, cb = open_store(a / "store.sqlite"), open_store(b / "store.sqlite")
    key, old = _file(ca, 1, "10.1016/j.test.2026.1", _paper())
    _, new = _file(cb, 1, "10.1016/j.test.2026.1", _renumbered())
    _file(ca, 2, "10.3390/mi15070851", json.loads((FIXTURES / "PMC11278924.docling.json").read_text()))  # a paper b does not hold
    live, mod = _node(old, "More than 90"), _node(old, "The compressive modulus")
    truth.save_labels(ca, live.node_id, [{"method": _section(old, "2.4 Cell viability").node_id, "verdict": "yes", "paragraph": _node(old, M_LIVE[:30]).node_id}, {"method": _section(old, "2.2 Mechanical testing").node_id, "verdict": "no"}], by="Karim", at="2026-10-04T00:00:00Z")
    truth.save_labels(ca, mod.node_id, [{"method": _section(old, "2.2 Mechanical testing").node_id, "verdict": "yes"}], by="Karim", at="2026-10-04T00:00:00Z")
    other = truth.queue(ca, n=50)["items"]
    elsewhere = next(i for i in other if i["prefix"] == "10.3390")
    truth.save_labels(ca, elsewhere["finding"]["node_id"], [{"verdict": "none"}], by="Karim", at="2026-10-04T00:00:00Z")
    ca.close(), cb.close()

    out = tmp_path / "labels.jsonl"
    assert truth.main(["--lib", str(a), "--export", str(out)]) == 0
    lines = [json.loads(l) for l in out.read_text("utf-8").splitlines()]
    assert len(lines) == 4 and all(l["lib"] == "a" for l in lines) and {l["verdict"] for l in lines} == {"yes", "no", "none"}

    assert truth.main(["--lib", str(b), "--import", str(out)]) == 0
    assert "1 labels name papers no library given holds" in capsys.readouterr().out
    cb = open_store(b / "store.sqlite")
    rows = {(r["finding"], r["method"]): r for r in truth.labels(cb)}
    assert len(rows) == 3
    want = {(_node(new, "More than 90").node_id, _section(new, "2.4 Cell viability").node_id), (_node(new, "More than 90").node_id, _section(new, "2.2 Mechanical testing").node_id), (_node(new, "The compressive modulus").node_id, _section(new, "2.2 Mechanical testing").node_id)}
    assert set(rows) == want  # stored under b's ids
    assert all(r["finding_now"] == r["finding"] and r["method_now"] == r["method"] and r["by"] == "Karim" for r in rows.values())
    assert rows[(_node(new, "More than 90").node_id, _section(new, "2.4 Cell viability").node_id)]["paragraph"] == _node(new, M_LIVE[:30]).node_id
    before = [tuple(r) for r in cb.execute("SELECT * FROM link_labels ORDER BY finding, method")]
    assert truth.import_labels(cb, lines) == {"imported": 3, "findings": 2, "anchored": 3, "unanchored": 0, "skipped": 1}
    assert [tuple(r) for r in cb.execute("SELECT * FROM link_labels ORDER BY finding, method")] == before  # twice is once
    cb.close()

    # the round trip: b's labels exported again are a's, under b's ids
    back = tmp_path / "back.jsonl"
    assert truth.main(["--lib", str(b), "--export", str(back)]) == 0
    strip = lambda rows: sorted((r["finding_text"], r["method_heading"], r["verdict"], r["paragraph_text"], r["by"], r["at"]) for r in rows)  # noqa: E731
    assert strip(json.loads(l) for l in back.read_text("utf-8").splitlines()) == strip(l for l in lines if l["paper"] == key)

    # measured over both libraries at once, on the command line
    capsys.readouterr()
    assert truth.main(["--lib", str(a), "--lib", str(b), "--measure", "--json"]) == 0
    m = json.loads(capsys.readouterr().out)
    assert m["findings"] == 5 and m["papers"] == 3 and m["verdicts"] == {"yes": 4, "no": 2, "none": 1}
    assert truth.main(["--lib", str(a), "--measure"]) == 0
    text = capsys.readouterr().out
    assert "findings labelled in 2 papers" in text and "precision by evidence" in text and "recall:" in text
    assert truth.main([]) == 2


# -- over the wire, and through a rebuild --------------------------------------------------------------


def test_the_worker_queues_saves_lists_and_measures_and_a_rebuild_keeps_the_labels(tmp_path):
    from litrag_parser.library import safe_key
    from litrag_parser.worker import Worker

    lib = tmp_path / "micromachines"
    assert by(talk(tmp_path, [{"id": "1", "op": "init", "name": "Micromachines"}]), "1")[0]["event"] == "library"
    conn = open_store(lib / "store.sqlite")
    key = file_paper(conn, title="x", file="paper.xml", sha256="aa", fmt="jats", doi="10.3390/mi15070851", pmid=None, pmcid=None, now="t").key
    shutil.copy(FIXTURES / "PMC11278924.jats.docling.json", lib / "parsed" / f"{safe_key(key)}.docling.json")
    _read(conn, key, json.loads((FIXTURES / "PMC11278924.jats.docling.json").read_text()))
    conn.close()

    events = talk(tmp_path, [{"id": "q", "op": "label_queue", "lib": "micromachines", "n": 3}])
    q = by(events, "q")[0]
    assert q["event"] == "label_queue" and len(q["items"]) == 3 and q["labelled"] == 0
    item = q["items"][0]
    yes = item["candidates"][0]
    labels = [{"method": yes["node_id"], "verdict": "yes", "paragraph": yes["paragraphs"][0]["node_id"]}] + [{"method": c["node_id"], "verdict": "no"} for c in item["candidates"][1:]]
    events = talk(tmp_path, [
        {"id": "l", "op": "label", "lib": "micromachines", "finding": item["finding"]["node_id"], "labels": labels, "by": "e2e"},
        {"id": "s", "op": "labels", "lib": "micromachines"},
        {"id": "t", "op": "truth", "lib": "micromachines"},
        {"id": "o", "op": "label_queue", "lib": "micromachines", "finding": item["finding"]["node_id"]},
        {"id": "x", "op": "label", "lib": "micromachines", "finding": item["finding"]["node_id"], "labels": [{"method": yes["node_id"], "verdict": "perhaps"}]},
    ])
    assert by(events, "l")[0]["event"] == "labelled" and len(by(events, "l")[0]["labels"]) == len(item["candidates"])
    rows = by(events, "s")[0]["labels"]
    assert {(r["method"], r["verdict"], r["by"]) for r in rows} == {(l["method"], l["verdict"], "e2e") for l in labels}
    t = by(events, "t")[0]
    assert t["event"] == "truth" and t["findings"] == 1 and t["paragraph"]["named"] == 1
    assert by(events, "o")[0]["items"][0]["labels"]
    assert by(events, "x")[0]["event"] == "error"

    Worker(tmp_path).do_rebuild({"id": "r", "op": "rebuild", "lib": "micromachines"})  # what the ingest thread does: the rows derived again
    events = talk(tmp_path, [{"id": "s", "op": "labels", "lib": "micromachines"}, {"id": "t", "op": "truth", "lib": "micromachines"}])
    after = by(events, "s")[0]["labels"]
    assert len(after) == len(rows) and all(r["finding_now"] and r["method_now"] is not None for r in after)
    assert by(events, "t")[0]["findings"] == 1 and by(events, "t")[0]["unanchored"] == {"findings": 0, "methods": 0, "paragraphs": 0}
