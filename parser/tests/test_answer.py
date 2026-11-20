"""Cited answers over the fixture paper: the passages retrieved and numbered, the model's claims
held against the passages they cite, a refusal said as one, and a model that cannot be had
answered with the passages and the reason. The model is a stand-in — a function, or a small
HTTP server speaking Ollama's `/api/chat` on 127.0.0.1 — so nothing here needs Ollama, and
nothing reaches the network."""

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from litrag_parser import answer, retrieve
from litrag_parser.citations import link_citations
from litrag_parser.edges import link_edges
from litrag_parser.library import create_library
from litrag_parser.store import file_paper, open_store, save_edges, save_refs, save_tree
from litrag_parser.tree import build_tree
from test_retrieve import FIXTURES, BagEmbedder


@pytest.fixture
def lib(tmp_path):
    lib = create_library(tmp_path, "Micromachines")
    conn = open_store(lib.store_path)
    key = file_paper(conn, title="x", file="a.pdf", sha256="aa", fmt="pdf", doi="10.3390/mi15070851", pmid=None, pmcid=None, now="t").key
    tree = build_tree(json.loads((FIXTURES / "PMC11278924.docling.json").read_text()), key)
    save_tree(conn, key, tree, parser="test", parsed_at="t", seconds=1.0)
    refs, cites = link_citations(tree, None)
    save_refs(conn, key, refs, cites)
    save_edges(conn, key, link_edges(tree, key, None))
    conn.commit()
    retrieve.embed_library(conn, emb())
    conn.close()
    return lib


def emb():
    """The bag of words under the embedder's own name, so the worker's OllamaEmbedder (asking the
    stand-in server below) meets the vectors stored here."""
    return BagEmbedder(model="nomic-embed-text")


QUESTION = "How was the PDMS microfluidic device fabricated?"


def first_sentence(text: str) -> str:
    return text.split(". ")[0].rstrip(".") + "."


# -- the check -------------------------------------------------------------------------------------


def test_numbers_are_read_as_written_less_thousands_separators():
    assert answer.numbers("2,730 ± 280 nm at 37 °C, 1.5 M and pH 7.4") == {"2730", "280", "37", "1.5", "7.4"}
    assert answer.numbers("−20 °C") == {"20"}
    assert "25" not in answer.numbers("2.5")
    assert answer.numbers("chains α1 and α2 of H4") == set()
    assert answer.numbers("chains α1 and α2 of H4", glued=True) == {"1", "2", "4"}
    assert answer.numbers("in 2024, 12.5 nm", glued=True) == {"2024", "12.5"}


def test_a_number_glued_to_a_letter_in_the_passage_holds_the_claim():
    passage = "The MW of the α1 and α2 chains was estimated as 138.0 kDa and 118.3 kDa, respectively."
    ok = answer.support("The molecular weight of the alpha 2 chain was estimated as 118.3 kDa.", [passage])
    assert ok["numbers_missing"] == []


def test_citation_markers_written_into_a_claim_are_not_numbers_it_states():
    assert answer.unmarked("Bester sturgeon skin collagen denatured at 26.8 °C [2].") == "Bester sturgeon skin collagen denatured at 26.8 °C."
    assert answer.unmarked("It was 225 nm [1, 2, 4] and 0.3 °C/min [5–7]") == "It was 225 nm and 0.3 °C/min"
    assert answer.unmarked("[Ca2+] rose in the (211) plane") == "[Ca2+] rose in the (211) plane"
    sources = [{"n": 2, "text": "The Tds of Bester sturgeon (skin: 26.8 °C, swim bladder: 32.9 °C) were lower."}]
    reply = {"status": "answered", "missing": "", "claims": [{"text": "Bester sturgeon skin collagen had a Tds of 26.8 °C [2].", "sources": [2]}]}
    claim = answer.check_claims(reply, sources)["claims"][0]
    assert claim["support"] == "supported" and claim["text"].endswith("26.8 °C.")


def test_the_cited_papers_title_and_headings_count_for_words_never_for_numbers():
    passage = "C3Gel began to absorb heat around 20 °C, with a distinct endothermic peak at 27.25 °C."
    claim = "The DSC peak of the recombinant humanized type III collagen cold gel was at 27.25 °C."
    title = "Structural characterization of recombinant humanized type III collagen cold gel"
    assert answer.support(claim, [passage])["support"] == "weak"
    held = answer.support(claim, [passage], [title, "3.1.7 DSC Thermal stability analysis"])
    assert held["support"] == "supported" and held["words"] >= answer.SUPPORT
    assert answer.support("The cold gel peaked at 31 °C.", [passage], [title, "Figure 31"])["numbers_missing"] == ["31"]


def test_support_wants_every_number_and_most_words_in_the_cited_passage():
    passage = "Collagen was crosslinked dehydrothermally at 110 °C for 24 h under vacuum."
    ok = answer.support("Collagen was crosslinked dehydrothermally at 110 °C for 24 h.", [passage])
    assert ok["support"] == "supported" and ok["numbers_missing"] == []
    wrong_number = answer.support("Collagen was crosslinked dehydrothermally at 120 °C for 24 h.", [passage])
    assert wrong_number["support"] == "weak" and wrong_number["numbers_missing"] == ["120"]
    elsewhere = answer.support("Fibroblasts proliferated faster on the genipin scaffolds.", [passage])
    assert elsewhere["support"] == "weak" and elsewhere["words"] < answer.SUPPORT


def test_a_claim_citing_nothing_given_is_left_out_and_said():
    sources = [{"n": 1, "text": "The device was cast in PDMS at 65 °C."}, {"n": 2, "text": "Cells were seeded at 1000 per well."}]
    reply = {"status": "answered", "missing": "", "claims": [
        {"text": "The device was cast in PDMS at 65 °C.", "sources": [1, 9]},
        {"text": "It was bonded by oxygen plasma.", "sources": [7]},
        {"text": "It took a week.", "sources": []},
    ]}
    out = answer.check_claims(reply, sources)
    assert [c["text"] for c in out["claims"]] == ["The device was cast in PDMS at 65 °C."]
    assert out["claims"][0]["sources"] == [1] and out["claims"][0]["struck"] == [9]
    assert [d["reason"] for d in out["dropped"]] == ["cites no passage it was given", "cites nothing"]
    assert out["status"] == "answered"


def test_the_status_says_what_the_claims_show():
    sources = [{"n": 1, "text": "The device was cast in PDMS."}]
    nothing_left = answer.check_claims({"status": "answered", "missing": "", "claims": [{"text": "x y z", "sources": [4]}]}, sources)
    assert nothing_left["status"] == "not_found" and not nothing_left["claims"]
    claims_under_refusal = answer.check_claims({"status": "not_found", "missing": "", "claims": [{"text": "The device was cast in PDMS.", "sources": [1]}]}, sources)
    assert claims_under_refusal["status"] == "partial"


def test_a_reply_not_in_the_shape_asked_for_is_none():
    assert answer.parse_reply("not json") is None
    assert answer.parse_reply('{"status": "maybe", "claims": []}') is None
    shaped = '{"status": "answered", "claims": [{"text": " A  b. ", "sources": [1, true, "2", 3]}, {"text": ""}], "missing": null}'
    assert answer.parse_reply(f"noise {shaped} noise") is None
    assert answer.parse_reply(shaped) == {"status": "answered", "claims": [{"text": "A b.", "sources": [1, 3]}], "missing": ""}


def test_the_summary_closes_every_sentence_with_its_citations():
    claims = [{"text": "PDMS was cast at 65 °C.", "sources": [1, 3]}, {"text": "It was bonded by plasma", "sources": [2]}]
    assert answer.summary("answered", claims, "") == "PDMS was cast at 65 °C [1, 3]. It was bonded by plasma [2]."
    assert answer.summary("not_found", [], "Nothing on bonding.") == "The library does not answer this. Nothing on bonding."


# -- the whole answer ---------------------------------------------------------------------------------


def test_ask_numbers_the_passages_and_keeps_only_what_they_hold(lib):
    seen = {}

    def model(name, system, user, **kw):
        seen.update(name=name, system=system, user=user)
        kw["on_tokens"](3)
        first = user.split("\n[1] ", 1)[1].split("\n", 1)[1].split("\n\n")[0]
        return {"content": json.dumps({"status": "answered", "missing": "", "claims": [
            {"text": first_sentence(first), "sources": [1]},
            {"text": "The chip glowed purple under moonlight on 31 Fridays.", "sources": [2]},
        ]}), "seconds": 1.0, "tokens": 40, "eval_seconds": 0.5, "prompt_tokens": 900}

    events = []
    a = answer.ask([lib], QUESTION, model="stand-in", embedder=emb(), chat_fn=model, on_progress=events.append)
    assert seen["name"] == "stand-in" and "nothing else" in seen["system"]
    assert "[1] " in seen["user"] and "Question: " + QUESTION in seen["user"]
    assert a["status"] == "answered" and a["libs"] == [lib.id] and a["model"] == "stand-in"
    assert [s["n"] for s in a["sources"]] == list(range(1, len(a["sources"]) + 1)) and len(a["sources"]) <= answer.K
    s1 = a["sources"][0]
    assert s1["lib"] == lib.id and s1["paper"] and s1["node_id"].startswith(s1["paper"]) and s1["doi"] == "10.3390/mi15070851"
    assert s1["cited"] and not any(s["cited"] for s in a["sources"][2:])
    supported, weak = a["claims"]
    assert supported["support"] == "supported" and weak["support"] == "weak" and "31" in weak["numbers_missing"]
    assert a["summary"].endswith("[2].") and "[1]" in a["summary"]
    assert a["timing"]["tokens_per_second"] == 80.0
    assert [e["stage"] for e in events][:2] == ["retrieve", "model"]


def test_a_refusal_is_said_as_one(lib):
    refuse = lambda *a, **k: {"content": json.dumps({"status": "not_found", "claims": [], "missing": "Nothing on krill."}), "seconds": 0.1, "tokens": 9}
    a = answer.ask([lib], "What is the denaturation temperature of krill collagen?", embedder=emb(), chat_fn=refuse)
    assert a["status"] == "not_found" and a["claims"] == [] and a["summary"].startswith("The library does not answer this.")
    assert a["sources"] and not any(s["cited"] for s in a["sources"])


def test_a_model_that_cannot_be_had_still_hands_back_the_passages(lib):
    def down(*a, **k):
        raise answer.ModelError("Ollama does not have qwen-x: ollama pull qwen-x")

    a = answer.ask([lib], QUESTION, embedder=emb(), chat_fn=down)
    assert a["status"] == "unanswered" and "ollama pull qwen-x" in a["error"] and a["sources"]
    garbled = answer.ask([lib], QUESTION, embedder=emb(), chat_fn=lambda *a, **k: {"content": "{", "seconds": 1, "tokens": 1200, "done_reason": "length"})
    assert garbled["status"] == "unanswered" and "cut short" in garbled["error"]


def test_any_number_of_passages_asked_for_is_held_to_what_the_window_takes(lib):
    seen = []

    def refuse(name, system, user, **kw):
        seen.append(user)
        return {"content": '{"status": "not_found", "claims": [], "missing": ""}', "seconds": 0, "tokens": 1, "prompt_tokens": 900}

    for k in (-1, 0):
        one = answer.ask([lib], QUESTION, embedder=emb(), chat_fn=refuse, k=k)
        assert len(one["sources"]) == 1 and len(seen) == 1 and "warning" not in one  # the model was asked, about one passage
        seen.clear()
    many = answer.ask([lib], QUESTION, embedder=emb(), chat_fn=refuse, k=500)
    assert 8 < len(many["sources"]) <= 20


def test_twenty_long_passages_make_a_prompt_that_leaves_the_reply_its_room():
    long = [{"n": n, "title": "A paper on gels", "year": "2026", "ancestry": ["3 Results", "3.1 Thermal stability"], "page": 4, "text": "collagen fibril " * 400} for n in range(1, 21)]
    chars = len(answer.SYSTEM) + len(answer.prompt(QUESTION, long))
    assert chars / 3 <= 8192 - 1200  # scientific prose runs to three characters a token at the worst; 1,200 tokens are the reply's
    eight = answer.prompt(QUESTION, long[:8])
    assert all(("collagen fibril " * 90) in block for block in eight.split("\n\n[")[1:])  # at eight, each keeps about 1,500 characters


def test_a_prompt_that_filled_the_window_is_said(lib):
    def full(prompt_tokens, tokens):
        return lambda *a, **k: {"content": '{"status": "not_found", "claims": [], "missing": ""}', "seconds": 1, "tokens": tokens, "prompt_tokens": prompt_tokens}

    assert "8,192-token window" in answer.ask([lib], QUESTION, embedder=emb(), chat_fn=full(8192, 20))["warning"]
    assert "warning" in answer.ask([lib], QUESTION, embedder=emb(), chat_fn=full(7500, 700))  # the reply ran past the window's end
    assert "warning" not in answer.ask([lib], QUESTION, embedder=emb(), chat_fn=full(2700, 300))


def test_nothing_retrieved_is_not_found_without_asking(lib, tmp_path):
    empty = create_library(tmp_path, "Empty")
    open_store(empty.store_path).close()

    def never(*a, **k):
        raise AssertionError("the model is not asked when nothing is retrieved")

    a = answer.ask([empty], QUESTION, embedder=emb(), chat_fn=never)
    assert a["status"] == "not_found" and a["sources"] == [] and "No passage" in a["missing"]
    with pytest.raises(ValueError):
        answer.ask([lib], "   ", embedder=emb(), chat_fn=never)


def test_several_libraries_give_a_passage_once(lib, tmp_path):
    import shutil

    twin = create_library(tmp_path, "Twin")
    shutil.copy(lib.store_path, twin.store_path)
    both = answer.ask([lib, twin], QUESTION, embedder=emb(), chat_fn=lambda *a, **k: {"content": '{"status": "not_found", "claims": [], "missing": ""}', "seconds": 0, "tokens": 1})
    texts = [s["text"] for s in both["sources"]]
    assert len(texts) == len(set(texts)) and both["libs"] == [lib.id, twin.id]
    assert answer.resolve(tmp_path, ["all"]) and {l.id for l in answer.resolve(tmp_path, ["micromachines", "Twin"])} == {lib.id, twin.id}
    with pytest.raises(ValueError):
        answer.resolve(tmp_path, ["nope"])


def test_the_model_is_the_request_then_the_environment_then_the_library(lib, monkeypatch):
    monkeypatch.delenv("LITRAG_ANSWER_MODEL", raising=False)
    assert answer.default_model(lib) == answer.DEFAULT_MODEL
    lib.manifest["ollama"] = {"answer": "gemma-x"}
    assert answer.default_model(lib) == "gemma-x"
    monkeypatch.setenv("LITRAG_ANSWER_MODEL", "qwen-y")
    assert answer.default_model(lib) == "qwen-y"


def test_a_case_of_the_grounding_set_is_judged_on_the_passage_and_on_the_refusal():
    sources = [{"n": 1, "doi": "10.1/A", "text": "It melted at 27.25 °C."}, {"n": 2, "doi": "10.1/b", "text": "Other."}]
    a = {"status": "answered", "claims": [{"text": "x", "sources": [1], "support": "supported"}], "dropped": [], "sources": sources, "timing": {"total": 2.0}}
    present = answer.judge_case({"id": "p", "answer": {"doi": "10.1/a", "contains": ["27.25"]}}, a)
    assert present["found"] and present["answered"] and present["cites_answer"] and present["supported"] == 1
    absent = answer.judge_case({"id": "q", "absent": True}, {**a, "status": "not_found", "claims": []})
    assert absent["refused"] and "found" not in absent
    failed = {**a, "status": "unanswered", "claims": [], "error": "Ollama does not have m: ollama pull m"}
    for case in ({"id": "q", "absent": True}, {"id": "p", "answer": {"doi": "10.1/a", "contains": ["27.25"]}}):
        j = answer.judge_case(case, failed)
        assert "ollama pull" in j["error"] and "refused" not in j and "found" not in j and "answered" not in j


def test_a_model_that_cannot_be_had_is_an_error_of_the_measure_never_a_refusal(lib, monkeypatch):
    lib.manifest["ollama"] = {"answer": "library-model"}
    lib.manifest_path.write_text(json.dumps(lib.manifest), "utf-8")
    monkeypatch.delenv("LITRAG_ANSWER_MODEL", raising=False)
    monkeypatch.setenv("LITRAG_OLLAMA_URL", "http://127.0.0.1:9")
    cases = [{"id": "absent", "lib": lib.id, "question": QUESTION, "absent": True},
             {"id": "present", "lib": lib.id, "question": QUESTION, "answer": {"doi": "10.3390/mi15070851", "contains": ["PDMS"]}}]
    rep = answer.measure(lib.root, cases)
    assert rep["errors"] == 2 and rep["model"] == "library-model"
    assert rep["absent"] == {"n": 0, "refused": 0, "answered_wrongly": 0}
    assert rep["present"]["n"] == 0 and rep["present"]["refused_wrongly"] == 0


# -- Ollama, stood in on 127.0.0.1 ------------------------------------------------------------------------


class FakeOllama:
    """`/api/tags`, `/api/embed` and a streamed `/api/chat`, as Ollama answers them. `think` makes
    the first chat refuse a `think` field the way a model without thinking does."""

    def __init__(self, reply: dict, think_refused: bool = False, breaks: str | None = None):
        self.reply, self.think_refused, self.chats, self.breaks = reply, think_refused, [], breaks
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send(200, {"models": [
                    {"name": "nomic-embed-text:latest", "size": 1, "details": {"family": "nomic-bert", "parameter_size": "137M"}, "capabilities": ["embedding"]},
                    {"name": "stand-in:9b", "size": 2, "details": {"family": "qwen", "parameter_size": "9B", "quantization_level": "Q4_K_M"}, "capabilities": ["completion"]},
                ]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/embed":
                    return self._send(200, {"embeddings": BagEmbedder().embed(body["input"])})
                fake.chats.append(body)
                if body.get("model") != "stand-in:9b":
                    return self._send(404, {"error": f"model '{body.get('model')}' not found"})
                if fake.think_refused and "think" in body:
                    return self._send(400, {"error": f"\"{body['model']}\" does not support thinking"})
                text = json.dumps(fake.reply)
                first = json.dumps({"message": {"content": text[:7]}, "done": False}).encode() + b"\n"
                if fake.breaks == "garbage":
                    return self._send(200, first + b"<html>502 Bad Gateway</html>\n", "application/x-ndjson")
                if fake.breaks == "dies":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(first), first))
                    self.wfile.flush()
                    self.close_connection = True
                    return
                lines = [{"message": {"content": text[i : i + 7]}, "done": False} for i in range(0, len(text), 7)]
                lines.append({"message": {"content": ""}, "done": True, "done_reason": "stop", "prompt_eval_count": 800, "eval_count": len(lines), "eval_duration": 1_000_000_000, "load_duration": 0})
                self._send(200, "".join(json.dumps(l) + "\n" for l in lines).encode(), "application/x-ndjson")

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def test_chat_streams_the_answer_and_says_what_went_wrong():
    fake = FakeOllama({"status": "not_found", "claims": [], "missing": "none"}, think_refused=True)
    try:
        ticks = []
        r = answer.chat("stand-in:9b", "s", "u", url=fake.url, on_tokens=ticks.append)
        assert json.loads(r["content"])["missing"] == "none" and r["prompt_tokens"] == 800 and ticks
        assert "think" in fake.chats[0] and "think" not in fake.chats[1]  # asked again without it
        assert fake.chats[1]["format"] == answer.SCHEMA and fake.chats[1]["options"]["temperature"] == 0
        with pytest.raises(answer.ModelError, match="ollama pull other"):
            answer.chat("other", "s", "u", url=fake.url)
        listed = answer.models(fake.url)
        assert [m["name"] for m in listed["models"]] == ["stand-in:9b"] and not listed["down"]  # the embedder is not a chat model
    finally:
        fake.close()
    with pytest.raises(answer.ModelError, match="not answering"):
        answer.chat("stand-in:9b", "s", "u", url="http://127.0.0.1:9", timeout=2)
    assert answer.models("http://127.0.0.1:9", timeout=1)["down"]


@pytest.mark.parametrize("breaks", ["garbage", "dies"])
def test_a_model_lost_part_way_through_its_answer_still_hands_back_the_passages(lib, breaks):
    fake = FakeOllama({"status": "not_found", "claims": [], "missing": "none"}, breaks=breaks)
    try:
        a = answer.ask([lib], QUESTION, model="stand-in:9b", url=fake.url, embedder=emb())
    finally:
        fake.close()
    assert a["status"] == "unanswered" and "part-way" in a["error"] and a["sources"] and a["claims"] == []


def test_the_worker_answers_ask_over_its_wire(lib, monkeypatch):
    fake = FakeOllama({"status": "answered", "missing": "", "claims": [{"text": "The device is described.", "sources": [1]}]})
    try:
        env = {**__import__("os").environ, "LITRAG_OLLAMA_URL": fake.url, "LITRAG_LANES_MODEL": "nomic-embed-text", "LITRAG_LANES": "off"}
        proc = subprocess.Popen([sys.executable, "-m", "litrag_parser.worker", f"--root={lib.root}"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env, cwd=Path(__file__).parent.parent)
        proc.stdin.write(json.dumps({"id": "m", "op": "models", "lib": lib.id}) + "\n")
        proc.stdin.write(json.dumps({"id": "a", "op": "ask", "lib": lib.id, "question": QUESTION, "model": "stand-in:9b", "k": 4}) + "\n")
        proc.stdin.flush()
        events = []
        for line in proc.stdout:
            ev = json.loads(line)
            events.append(ev)
            if ev.get("id") == "a" and ev["event"] in ("answer", "error"):
                break
        proc.stdin.write(json.dumps({"id": "q", "op": "quit"}) + "\n")
        proc.stdin.flush()
        proc.wait(timeout=30)
    finally:
        fake.close()
    models = next(e for e in events if e.get("id") == "m")
    assert models["event"] == "models" and [m["name"] for m in models["models"]] == ["stand-in:9b"]
    done = events[-1]
    assert done["event"] == "answer", done
    assert done["status"] == "answered" and len(done["sources"]) <= 4 and done["claims"][0]["sources"] == [1]
    assert done["retrieval"]["embedder"]["meaning"] == "ok"  # the question was embedded through the stand-in
    assert {e["stage"] for e in events if e["event"] == "asking"} >= {"retrieve", "model"}
