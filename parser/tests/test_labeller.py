"""The truth set labelled by the local model for a person to audit: the question it is asked
(the methods numbered, the finding last), its answer read and held to what it was shown, its
labels kept apart from a person's, the person's queue offering its findings first without
saying what it answered, the two compared, and its labels counted only once they agree."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from litrag_parser import labeller, truth
from litrag_parser.store import open_store

from test_edges import M_FAB, M_LIVE, M_MECH, R_LIVE, R_MOD
from test_truth import PAPERS, _file, _node, _section


@pytest.fixture
def library(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    trees = {}
    for n, (doi, doc) in enumerate(PAPERS, 1):
        key, tree = _file(conn, n, doi, doc())
        trees[key] = tree
    yield conn, trees
    conn.close()


def _synthetic(trees):
    key = next(k for k in trees if k.startswith("doi:10.1016"))
    return key, trees[key]


def _by_keyword(prompt, model, url):
    """A model that names the method whose paragraph shares the finding's telling word."""
    methods, finding = prompt.split("\nMETHODS:\n", 1)[1].split("\n\nFINDING (", 1)
    f = finding.lower()
    word = next((w for w in ("live/dead", "modulus", "swelling") if w in f), None)
    named, paras = [], []
    for line in methods.splitlines():
        if line.startswith("[M") and "." in line.split("]")[0] and word and word in line.lower():
            label = line[1:].split("]")[0]
            named.append(label.split(".")[0])
            paras.append(label)
    return {"content": json.dumps({"methods": named, "paragraphs": paras}), "prompt_tokens": 1000, "seconds": 0.5}


# -- the question -------------------------------------------------------------------------------------


def test_the_prompt_numbers_the_methods_and_their_paragraphs_and_puts_the_finding_last(library):
    conn, trees = library
    key, tree = _synthetic(trees)
    p = truth._Papers(conn).get(key)
    live = _node(tree, "More than 90")
    prompt, ids = labeller.prompt_for(conn, p.candidates, live)
    assert prompt.startswith(labeller.PROMPT) and labeller.RULE in prompt
    assert "[M1] 2.1 Scaffold fabrication" in prompt and f"[M1.1] {M_FAB}" in prompt and f"[M4.1] {M_LIVE}" in prompt
    assert ids["M2"] == _section(tree, "2.2 Mechanical testing").node_id and ids["M2.1"] == _node(tree, M_MECH[:30]).node_id
    assert prompt.index("METHODS:") < prompt.index("FINDING (3 Results") and prompt.rstrip().endswith("ethidium homodimer.")  # the caption of Figure 4, last
    assert f"FINDING (3 Results" in prompt and R_LIVE in prompt and "The figures it cites:\n- Figure 4. Live/dead staining" in prompt
    other = labeller.prompt_for(conn, p.candidates, _node(tree, "The compressive modulus"))[0]
    assert other.split("FINDING (")[0] == prompt.split("FINDING (")[0]  # one paper's findings share everything before the finding


def test_long_methods_are_sent_as_the_opening_words_of_each_paragraph(library, monkeypatch):
    conn, trees = library
    key, tree = _synthetic(trees)
    p = truth._Papers(conn).get(key)
    monkeypatch.setattr(labeller, "BUDGET_WORDS", 20)
    monkeypatch.setattr(labeller, "EXCERPT_WORDS", 5)
    text, _ = labeller.methods_text(p.candidates)
    assert "[M2] 2.2 Mechanical testing" in text and "[M2.1] Compressive modulus was measured on …" in text


def test_the_model_is_given_the_rule_the_panel_shows_a_person():
    from pathlib import Path

    ts = (Path(__file__).parents[2] / "app" / "src" / "renderer" / "truth.ts").read_text("utf-8")
    assert f"'{labeller.RULE}'" in ts.replace("\n  '", "'")


@pytest.mark.parametrize("content, want", [
    ('{"methods": ["M2", "M4"], "paragraphs": ["M4.1"]}', (["m2", "m4"], {"m4": "m4.1"})),
    ('```json\n{"methods": ["[M2]"], "paragraphs": []}\n```', (["m2"], {})),
    ('{"methods": ["M2.1"], "paragraphs": []}', (["m2"], {"m2": "m2.1"})),  # a paragraph's number names its method
    ('{"methods": [], "paragraphs": ["M2.1"]}', ([], {})),  # none, and a paragraph of nothing named is dropped
    ('{"methods": ["M2", "M2", "M9"], "paragraphs": ["M4.1", "M2.1", "M2.2"]}', (["m2"], {"m2": "m2.1"})),
    ('{"methods": ["M9"], "paragraphs": []}', None),  # it named something, and nothing it was shown
    ('I think M2', None),
    ('{"answer": "M2"}', None),
])
def test_the_answer_is_held_to_what_the_model_was_shown(content, want):
    ids = {"M1": "m1", "M1.1": "m1.1", "M2": "m2", "M2.1": "m2.1", "M2.2": "m2.2", "M4": "m4", "M4.1": "m4.1"}
    got = labeller.parse(content, ids)
    assert (None if got is None else (got["methods"], got["paragraphs"])) == want


# -- the labels ------------------------------------------------------------------------------------


def test_the_model_labels_the_draw_once_and_another_model_replaces_it(library):
    conn, trees = library
    key, tree = _synthetic(trees)
    calls = []

    def asker(prompt, model, url):
        calls.append(model)
        return _by_keyword(prompt, model, url)

    out = labeller.label(conn, n=8, seed=6, per_paper=3, model="fake:1b", asker=asker)
    drawn = truth.draw(truth._Papers(conn), 8, 6, 3)
    assert out["findings"] == 8 and out["asked"] == 8 and out["labelled"] + out["unreadable"] == 8 and out["prompt_tokens"] == 8000
    rows = [dict(r) for r in conn.execute("SELECT * FROM model_labels")]
    assert {r["finding"] for r in rows} == {f.node_id for _, f in drawn} and {r["by"] for r in rows} == {"fake:1b"}
    assert conn.execute("SELECT COUNT(*) FROM link_labels").fetchone()[0] == 0  # a person's table untouched
    live = _node(tree, "More than 90")  # in this seed's draw
    mine = {r["method_heading"]: (r["verdict"], r["paragraph"]) for r in rows if r["finding"] == live.node_id}
    assert mine["2.4 Cell viability"] == ("yes", _node(tree, M_LIVE[:30]).node_id) and mine["2.2 Mechanical testing"] == ("no", None)
    assert all(v == ("no", None) for h, v in mine.items() if h != "2.4 Cell viability")
    again = labeller.label(conn, n=8, seed=6, per_paper=3, model="fake:1b", asker=asker)
    assert again["asked"] == 0 and again["already"] == 8 and len(calls) == 8  # running it twice changes nothing
    other = labeller.label(conn, n=8, seed=6, per_paper=3, model="fake:2b", asker=asker)
    assert other["asked"] == 8 and {r[0] for r in conn.execute("SELECT DISTINCT by FROM model_labels")} == {"fake:2b"}


def test_the_model_also_labels_what_a_person_labelled_outside_its_draw(library):
    conn, trees = library
    key, tree = _synthetic(trees)
    papers = truth._Papers(conn)
    drawn = {f.node_id for _, f in truth.draw(papers, 2, 0, 1)}
    outside = next(f for f in papers.get(key).findings if f.node_id not in drawn)
    truth.save_labels(conn, outside.node_id, [{"verdict": "none"}], by="Karim")
    got = [f.node_id for _, f in labeller.sample(conn, truth._Papers(conn), 2, 0, 1)]
    assert got[:2] == [f.node_id for _, f in truth.draw(truth._Papers(conn), 2, 0, 1)] and got[2:] == [outside.node_id]


def test_an_unreadable_answer_stores_nothing_and_ollama_down_says_why(library):
    conn, _ = library
    out = labeller.label(conn, n=2, model="fake:1b", asker=lambda p, m, u: {"content": "M2, I believe"})
    assert out["unreadable"] == 2 and out["labelled"] == 0 and conn.execute("SELECT COUNT(*) FROM model_labels").fetchone()[0] == 0
    with pytest.raises(RuntimeError, match="not answering at http://127.0.0.1:9.*ollama pull fake:1b"):
        labeller.label(conn, n=2, model="fake:1b", url="http://127.0.0.1:9")


# -- the audit -------------------------------------------------------------------------------------


def test_the_queue_offers_the_models_findings_first_and_never_its_answer(library):
    conn, trees = library
    labeller.label(conn, n=4, seed=5, per_paper=2, model="fake:1b", asker=_by_keyword)
    model_order = [r[0] for r in conn.execute("SELECT DISTINCT finding FROM model_labels ORDER BY at, paper, finding")]
    q = truth.queue(conn, n=10, seed=0, per_paper=5)  # another seed: the model's findings still come first
    assert q["audit"] == 4 and [i["finding"]["node_id"] for i in q["items"][:4]] == model_order
    assert all(i["labels"] == [] for i in q["items"])  # blind: an item carries a person's labels only
    ids = [i["finding"]["node_id"] for i in q["items"]]
    assert len(ids) == len(set(ids)) == 10
    truth.save_labels(conn, model_order[0], [{"verdict": "none"}], by="Karim")
    q = truth.queue(conn, n=10, seed=0, per_paper=5)
    assert q["audit"] == 3 and model_order[0] not in [i["finding"]["node_id"] for i in q["items"]]


def test_agreement_and_the_model_counted_only_once_it_stands(library, monkeypatch):
    conn, trees = library
    key, tree = _synthetic(trees)
    live, mod = _node(tree, "More than 90"), _node(tree, "The compressive modulus")
    via, mech, swell = (_section(tree, h) for h in ("2.4 Cell viability", "2.2 Mechanical testing", "2.3 Swelling"))
    cands = [c.node_id for c in truth._Papers(conn).get(key).candidates]

    def labels(yes, para=None):
        return [{"method": c, "verdict": "yes" if c in yes else "no", **({"paragraph": para} if para and c in yes else {})} for c in cands]

    p = truth._Papers(conn).get(key)
    at = "2026-10-04T00:00:00Z"
    para = _node(tree, M_LIVE[:30]).node_id
    truth._write(conn, p, live.node_id, labeller.rows_of(p, live, {"methods": [via.node_id], "paragraphs": {via.node_id: para}}, "fake:1b", at), truth._Papers(conn), table="model_labels")
    truth._write(conn, p, mod.node_id, labeller.rows_of(p, mod, {"methods": [mech.node_id], "paragraphs": {}}, "fake:1b", at), truth._Papers(conn), table="model_labels")
    assert truth.report(conn)["model"]["audited"] == 0

    truth.save_labels(conn, live.node_id, labels({via.node_id}, para), by="Karim")  # agrees, paragraph too
    truth.save_labels(conn, mod.node_id, labels({mech.node_id, swell.node_id}), by="Karim")  # the person saw a second method
    a = truth.agreement(conn)
    assert (a["labelled"], a["audited"], a["agree"], a["agreement"]) == (2, 2, 1, 0.5) and a["models"] == {"fake:1b": 2}
    assert a["methods"] == {"both_yes": 2, "both_no": 5, "model_only": 0, "person_only": 1, "agreement": 0.875}
    assert a["paragraphs"] == {"both": 1, "same": 1, "agreement": 1.0}
    assert a["disagreements"] == [{"finding": mod.node_id, "paper": key, "text": mod.text[:200], "person": ["2.2 Mechanical testing", "2.3 Swelling"], "model": ["2.2 Mechanical testing"]}]
    r = truth.report(conn)
    assert not r["model"]["stands"] and r["model"]["measure"] is None and r["source"] == "person"

    monkeypatch.setattr(truth, "AUDIT_MIN", 1)
    monkeypatch.setattr(truth, "AGREEMENT_GATE", 0.5)
    other = next(f for f in p.findings if f.node_id not in (live.node_id, mod.node_id))
    truth._write(conn, p, other.node_id, labeller.rows_of(p, other, {"methods": [], "paragraphs": {}}, "fake:1b", at), truth._Papers(conn), table="model_labels")
    r = truth.report(conn)
    assert r["model"]["stands"] and r["findings"] == 2
    both = r["model"]["measure"]
    assert both["source"] == "both" and both["findings"] == 3 and both["none"] == 1  # the person's two, and the model's third
    assert truth.measure(conn, source="model")["findings"] == 3
    with pytest.raises(ValueError):
        truth.measure(conn, source="anyone")


# -- Ollama, and the worker ---------------------------------------------------------------------------


class _Ollama(BaseHTTPRequestHandler):
    seen: list = []
    answer = {"methods": ["M2"], "paragraphs": ["M2.1"]}
    missing = False

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append(body)
        if type(self).missing:
            self.send_response(404)
            out = {"error": f"model '{body['model']}' not found"}
        else:
            self.send_response(200)
            out = {"message": {"content": json.dumps(type(self).answer)}, "prompt_eval_count": 1234, "eval_count": 20}
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(out).encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def ollama():
    _Ollama.seen, _Ollama.missing = [], False
    srv = HTTPServer(("127.0.0.1", 0), _Ollama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_ask_holds_the_model_to_the_schema_with_thinking_off(ollama):
    got = labeller.ask("METHODS:\n[M1] x\n\nFINDING (y):\nz", "fake:1b", ollama)
    body = _Ollama.seen[0]
    assert body["format"] == labeller.SCHEMA and body["think"] is False and body["stream"] is False
    assert body["options"]["temperature"] == 0 and body["options"]["num_ctx"] >= 8192 and body["model"] == "fake:1b"
    assert json.loads(got["content"]) == _Ollama.answer and got["prompt_tokens"] == 1234
    _Ollama.missing = True
    with pytest.raises(RuntimeError, match=r"not found.*ollama pull fake:1b"):
        labeller.ask("x", "fake:1b", ollama)


def test_the_worker_labels_with_progress_and_the_truth_op_reports_the_model(tmp_path, ollama, monkeypatch, capsys):
    import shutil

    from litrag_parser.library import safe_key
    from litrag_parser.store import file_paper
    from litrag_parser.worker import Worker

    from test_truth import FIXTURES, _read, by, talk

    monkeypatch.setenv("LITRAG_OLLAMA_URL", ollama)
    lib = tmp_path / "micromachines"
    assert by(talk(tmp_path, [{"id": "1", "op": "init", "name": "Micromachines"}]), "1")[0]["event"] == "library"
    conn = open_store(lib / "store.sqlite")
    key = file_paper(conn, title="x", file="paper.xml", sha256="aa", fmt="jats", doi="10.3390/mi15070851", pmid=None, pmcid=None, now="t").key
    shutil.copy(FIXTURES / "PMC11278924.jats.docling.json", lib / "parsed" / f"{safe_key(key)}.docling.json")
    _read(conn, key, json.loads((FIXTURES / "PMC11278924.jats.docling.json").read_text()))
    conn.close()

    Worker(tmp_path).do_model_label({"id": "m", "op": "model_label", "lib": "micromachines", "n": 3, "model": "fake:1b"})  # what the ingest thread does
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    progress = [e for e in events if e["event"] == "progress"]
    done = events[-1]
    assert progress[0]["op"] == "model_label" and progress[0]["total"] == 3 and "fake:1b" in progress[0]["label"]
    assert done["event"] == "done" and done["op"] == "model_label" and done["labelled"] == 3 and done["model"] == "fake:1b"
    assert len(_Ollama.seen) == 3

    events = talk(tmp_path, [{"id": "t", "op": "truth", "lib": "micromachines"}, {"id": "q", "op": "label_queue", "lib": "micromachines", "n": 5}])
    t, q = by(events, "t")[0], by(events, "q")[0]
    assert t["model"]["labelled"] == 3 and t["model"]["audited"] == 0 and not t["model"]["stands"] and t["findings"] == 0
    assert q["audit"] == 3 and all(not i["labels"] for i in q["items"])
