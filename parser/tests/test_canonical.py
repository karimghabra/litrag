import io
import json
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from litrag_parser import canonical, meaning
from litrag_parser.canonical import Verdicts, canonical_tree, mapping, mechanism_of, skeletons, types_overview
from litrag_parser.facets import normalise
from litrag_parser.store import file_paper, open_store, save_tree, set_type
from litrag_parser.tree import build_tree

FIXTURES = Path(__file__).parent / "fixtures"
BODY = ["abstract", "introduction", "methods", "results", "discussion"]


def _store(tmp_path, keys=("10.1/a", "10.1/b")):
    conn = open_store(tmp_path / "lib" / "store.sqlite")
    doc = json.loads((FIXTURES / "PMC11278924.docling.json").read_text())
    out = []
    for i, doi in enumerate(keys):
        key = file_paper(conn, title="x", file=f"{i}.pdf", sha256=f"s{i}", fmt="pdf", doi=doi, pmid=None, pmcid=None, now="t").key
        save_tree(conn, key, build_tree(doc, key), parser="test", parsed_at="t", seconds=1.0)
        set_type(conn, key, "research", "record", "Journal Article")
        out.append(key)
    return conn, out


def _drop(conn, node_ids):
    """A section's rows and everything under it, gone."""
    with conn:
        for nid in node_ids:
            conn.execute("WITH RECURSIVE sub(id) AS (SELECT ? UNION ALL SELECT n.node_id FROM nodes n JOIN sub ON n.parent = sub.id) DELETE FROM nodes WHERE node_id IN (SELECT id FROM sub)", (nid,))


def test_the_skeleton_is_the_lanes_in_order_with_furniture_at_the_ends(tmp_path):
    conn, _ = _store(tmp_path)
    sk = skeletons(conn)
    assert list(sk) == ["research"]
    r = sk["research"]
    assert r["papers"] == 2 and r["expected"] == ["introduction", "methods", "results", "discussion"]
    assert [s["slot"] for s in r["slots"]] == BODY
    assert all(s["share"] == 1.0 for s in r["slots"])
    assert [s["status"] for s in r["slots"]] == ["typical", "expected", "expected", "expected", "expected"]
    positions = [s["median_position"] for s in r["slots"]]
    assert positions == sorted(positions) and positions[0] == 0.0
    assert [s["slot"] for s in r["furniture"]["head"]] == ["front"] and [s["slot"] for s in r["furniture"]["tail"]] == ["references"]
    discussion = r["slots"][-1]
    assert [c["name"] for c in discussion["canonical"]] == ["Conclusions", "Discussion"]  # two names in one slot, each with its own place
    by_name = {c["name"]: c for c in discussion["canonical"]}
    assert by_name["Discussion"]["median_position"] < by_name["Conclusions"]["median_position"]
    assert discussion["median_sections"] == 2.0
    methods = r["slots"][2]
    assert methods["examples"][0]["heading"] == "materials and methods" and methods["words_median"] > 0


def test_every_section_maps_by_a_mechanism_read_from_the_rows(tmp_path):
    conn, (a, _) = _store(tmp_path)
    m = mapping(conn, a)
    assert m["paper"]["type"] == "research" and m["paper"]["key"] == a
    tops = {e["heading"]: e for e in m["sections"]}
    assert tops["Front matter"]["mechanism"] == "front" and tops["Front matter"]["furniture"] == "head"
    assert tops["Abstract"]["mechanism"] == "vocabulary" and tops["Abstract"]["evidence"] == "replay"
    assert tops["2. Materials and Methods"]["slot"] == "methods" and tops["2. Materials and Methods"]["canonical_by"] == "table"
    assert tops["3. Experimental Results"]["mechanism"] == "vocabulary"
    assert tops["References"]["furniture"] == "tail"
    subs = tops["2. Materials and Methods"]["children"]
    assert len(subs) == 8 and {c["mechanism"] for c in subs} == {"inherited"}
    assert subs[0]["canonical"] == "Materials" and subs[0]["words"] > 0
    assert {s["slot"]: s["status"] for s in m["slots"]} == {**{lane: "matched" for lane in BODY}, "front": "furniture", "references": "furniture"}
    assert m["order"] == []
    assert 0.0 <= m["unassigned_words_share"] < 0.1
    json.dumps(m)  # every answer is JSON


def test_a_deleted_section_is_a_missing_slot_and_an_empty_one_in_the_canonical_tree(tmp_path):
    conn, (a, b) = _store(tmp_path)
    ids = [r[0] for r in conn.execute("SELECT node_id FROM nodes WHERE paper = ? AND type = 'section' AND depth = 1 AND role = 'discussion'", (b,))]
    assert len(ids) == 2
    _drop(conn, ids)
    sk = skeletons(conn)["research"]
    assert {s["slot"]: s["share"] for s in sk["slots"]}["discussion"] == 0.5
    m = mapping(conn, b)
    status = {s["slot"]: s for s in m["slots"]}
    assert status["discussion"]["status"] == "missing" and status["discussion"]["sections"] == [] and status["discussion"]["near"] == []
    assert status["results"]["status"] == "matched"
    t = canonical_tree(conn, b)
    slots = {s["slot"]: s for s in t["slots"]}
    assert slots["discussion"]["status"] == "missing" and slots["discussion"]["sections"] == []
    full = canonical_tree(conn, a)
    assert [s["slot"] for s in full["slots"]] == ["front", *BODY, "other", "references"]
    assert [x["heading"] for x in {s["slot"]: s for s in full["slots"]}["discussion"]["sections"]] == ["4. Discussion", "5. Conclusions"]
    assert {s["slot"]: s for s in full["slots"]}["other"]["sections"] == []


def test_an_order_the_type_does_not_keep_is_an_anomaly(tmp_path):
    conn, (a, b, _) = _store(tmp_path, keys=("10.1/a", "10.1/b", "10.1/c"))
    methods = conn.execute("SELECT node_id FROM nodes WHERE paper = ? AND depth = 1 AND role = 'methods'", (a,)).fetchone()[0]
    with conn:
        conn.execute("UPDATE nodes SET ordinal = 99 WHERE node_id = ?", (methods,))  # Nature's order: the methods after the discussion
    m = mapping(conn, a)
    assert [(o["slot"], o["after"]) for o in m["order"]] == [("methods", "discussion")]
    assert mapping(conn, b)["order"] == []


def test_a_slot_the_type_lacks_is_extra(tmp_path):
    conn, (a, b, _) = _store(tmp_path, keys=("10.1/a", "10.1/b", "10.1/c"))
    intro = conn.execute("SELECT node_id FROM nodes WHERE paper = ? AND depth = 1 AND role = 'introduction'", (a,)).fetchone()[0]
    with conn:
        conn.execute("UPDATE nodes SET role = 'other', heading = 'Collagen in the tendon' WHERE node_id = ?", (intro,))
    m = mapping(conn, a)
    status = {s["slot"]: s for s in m["slots"]}
    assert status["other"]["status"] == "extra" and status["other"]["sections"] == [intro]
    assert status["introduction"]["status"] == "missing"
    assert {e["node_id"]: e["mechanism"] for e in m["sections"]}[intro] == "none"


class _Stub:
    """A verdict store that answers from a dict."""

    def __init__(self, answers):
        self.answers = answers

    def get(self, kind, text):
        return self.answers.get((kind, text))


def _sec(heading, role, **kw):
    return {"node_id": kw.pop("node_id", "p#section-1"), "heading": heading, "role": role, "label": kw.pop("label", "section_header"), **kw}


@pytest.mark.parametrize("section,parent,verdicts,want,guess", [
    (_sec("Methods", "other", reasons={"recognised_by": ["vocabulary"], "withheld": "methods"}, guess="methods", confidence=0.3333), None, None, "withheld", "methods"),
    (_sec("Results", "other", reasons={"heading": "results", "paragraphs": "methods", "cosine": 0.7, "margin": 0.1}), None, None, "disagreement", "results"),
    (_sec("Tendon cells", "results", reasons={"vocabulary": "other", "embedder": "results"}), None, None, "embedder", None),
    (_sec("Tendon cells", "results", reasons={"something": "new"}), None, None, "unknown", None),
    (_sec("Materials and methods", "methods", label="built"), None, None, "built", None),
    (_sec("Results", "results", label="built", node_id="p#section-2#section-outline-1"), None, None, "outline", None),
    (_sec("2.1 Cell culture", "methods"), "methods", None, "inherited", None),
    (_sec("Front matter", "other"), None, None, "front", None),
    (_sec("3. (heading not detected)", "other"), None, None, "untitled", None),
    (_sec("2. Materials and Methods", "methods"), None, None, "vocabulary", None),
    (_sec("Case presentation", "results"), None, None, "catalogue", None),
    (_sec("6 Summary", "discussion"), None, None, "position", None),
    (_sec("Tenocyte phenotype", "results"), None, _Stub({("heading", "tenocyte phenotype"): {"name": "results", "cosine": 0.81, "margin": 0.1, "top": "results"}}), "embedder", None),
    (_sec("3. Time Series Early Warning Methods", "methods"), None, None, "vocabulary", None),
    (_sec("8. Regulatory and Ethical Considerations", "other"), None, _Stub({("heading", "regulatory and ethical considerations"): {"name": "back", "cosine": 0.8, "margin": 0.1, "top": "back"}}), "numbered", "back"),
    (_sec("Immune cells in the ageing ventricle", "other"), None, None, "none", None),
    (_sec("Immune cells in the ageing ventricle", "other"), None, _Stub({("heading", "immune cells in the ageing ventricle"): {"name": "other", "cosine": 0.66, "margin": 0.01, "top": "results"}}), "none", "results"),
    (_sec("Tenocyte phenotype", "results"), None, None, "unknown", None),  # an older row: nothing says what named it, and it is not presumed
])
def test_the_mechanism_is_read_never_presumed(section, parent, verdicts, want, guess):
    m = mechanism_of(section, parent, verdicts)
    assert m["mechanism"] == want and m["mechanism"] in canonical.MECHANISMS
    assert m["guess"] == guess
    if section.get("reasons"):
        assert m["evidence"] == "reasons" and m["detail"] == section["reasons"]


def test_withheld_keeps_the_rows_confidence():
    m = mechanism_of(_sec("Methods", "other", reasons={"recognised_by": ["vocabulary"], "withheld": "methods"}, confidence=0.3333))
    assert m["confidence"] == 0.3333 and m["guess"] == "methods"


def test_stored_verdicts_are_read_under_todays_space_and_rule(tmp_path, monkeypatch):
    monkeypatch.delenv("LITRAG_LANES_MODEL", raising=False)
    path = tmp_path / "lanes.sqlite"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE verdicts (kind TEXT NOT NULL, key TEXT NOT NULL, model TEXT NOT NULL, name TEXT NOT NULL, score REAL, margin REAL, at TEXT, ranked TEXT, rule TEXT, PRIMARY KEY (kind, key, model))")
    h = meaning.KINDS["heading"]
    model = f"{meaning.DEFAULT_MODEL}@{h.space()}"
    c.execute("INSERT INTO verdicts VALUES ('heading', ?, ?, 'methods', 0.8, 0.1, 't', NULL, ?)", (normalise("Tissue processing"), model, h.rule()))
    c.execute("INSERT INTO verdicts VALUES ('heading', ?, ?, 'results', 0.8, 0.1, 't', NULL, 'another')", (normalise("Old rule"), model))
    c.execute("INSERT INTO verdicts VALUES ('heading', ?, ?, 'methods', 0.8, 0.1, 't', NULL, ?)", (normalise("Other space"), "nomic-embed-text@00000000", h.rule()))
    c.commit()
    c.close()
    v = Verdicts(path)
    assert v.get("heading", "tissue processing")["name"] == "methods"
    assert v.get("heading", "old rule") is None  # a rule no longer in force is no answer
    assert v.get("heading", "other space") is None  # nor is another space's
    assert mechanism_of(_sec("Tissue processing", "methods"), None, v)["mechanism"] == "embedder"
    v.close()


def test_types_overview_and_the_command(tmp_path):
    conn, (a, _) = _store(tmp_path)
    over = types_overview(conn)
    assert over == [{"type": "research", "papers": 2, "parsed": 2, "mean_confidence": None, "with_methods": 1.0, "formats": {"pdf": 2}, "subtypes": {}}]
    conn.close()
    store = tmp_path / "lib" / "store.sqlite"
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert canonical.main(["--store", str(store), "--json", "--lanes", "off"]) == 0
    got = json.loads(buf.getvalue())
    assert got["types"][0]["type"] == "research" and [s["slot"] for s in got["skeletons"]["research"]["slots"]] == BODY
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert canonical.main(["--store", str(store), "--paper", a, "--json"]) == 0
    got = json.loads(buf.getvalue())
    assert got["mapping"]["paper"]["key"] == a and got["canonical_tree"]["slots"][0]["slot"] == "front"


def test_a_store_from_before_the_near_misses_reads_as_unmeasured(tmp_path):
    conn, (a, _) = _store(tmp_path)
    conn.close()
    old = sqlite3.connect(tmp_path / "old.sqlite")
    old.execute("ATTACH DATABASE ? AS new", (str(tmp_path / "lib" / "store.sqlite"),))
    old.execute("CREATE TABLE papers AS SELECT key, doi, title, format, status, has_methods, type, subtype FROM new.papers")  # no confidence either
    old.execute("CREATE TABLE nodes AS SELECT node_id, paper, parent, ordinal, depth, type, label, level, role, heading, ancestry, text, page, canonical FROM new.nodes")
    old.commit()
    m = mapping(old, a)
    assert m["paper"]["confidence"] is None
    assert all(e["confidence"] is None and e["guess"] is None for e in m["sections"])
    assert [s["slot"] for s in m["slots"] if s["status"] == "matched"] == BODY
