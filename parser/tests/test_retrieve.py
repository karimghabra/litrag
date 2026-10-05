"""Chunkless retrieval over the fixture paper: units, vectors that are rows and cascade with
their nodes, words and meaning fused, and every hit handed back with its context from the
tree — its neighbours, its section, the method its `measured_by` edge names. The embedder is
a hashed bag of words: it proves the plumbing, not the ranking quality."""

import json
import re
import zlib
from collections import Counter
from pathlib import Path

import pytest

from litrag_parser import retrieve
from litrag_parser.citations import link_citations
from litrag_parser.edges import link_edges
from litrag_parser.store import file_paper, open_store, save_edges, save_refs, save_tree
from litrag_parser.tree import build_tree

FIXTURES = Path(__file__).parent / "fixtures"


class BagEmbedder:
    """Words of three letters or more hashed into `dims` buckets, normalised; the task prefix
    stripped first. `down` makes it answer None, as Ollama unreachable does."""

    def __init__(self, dims: int = 2048, model: str = "bag"):
        self.dims, self.model, self.down, self.calls, self.texts = dims, model, False, 0, 0

    def embed(self, texts):
        self.calls += 1
        if self.down:
            return None
        self.texts += len(texts)
        out = []
        for t in texts:
            t = re.sub(r"^(search_document|search_query): ", "", t)
            v = [0.0] * self.dims
            for w in re.findall(r"[a-z]{3,}", t.lower()):
                v[zlib.crc32(w.encode()) % self.dims] += 1.0
            n = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / n for x in v])
        return out


@pytest.fixture
def store(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    key = file_paper(conn, title="x", file="a.pdf", sha256="aa", fmt="pdf", doi="10.3390/mi15070851", pmid=None, pmcid=None, now="t").key
    tree = build_tree(json.loads((FIXTURES / "PMC11278924.docling.json").read_text()), key)
    save_tree(conn, key, tree, parser="test", parsed_at="t", seconds=1.0)
    refs, cites = link_citations(tree, None)
    save_refs(conn, key, refs, cites)
    save_edges(conn, key, link_edges(tree, key, None))
    conn.commit()
    yield conn, key, tree
    conn.close()


def _unique_words(us, target, n=5):
    df = Counter()
    for u in us:
        df.update(set(re.findall(r"[a-z]{6,}", u["text"].lower())))
    return [w for w in dict.fromkeys(re.findall(r"[a-z]{6,}", target["text"].lower())) if df[w] == 1][:n]


def test_units_are_prose_and_leave_out_references_and_furniture(store):
    conn, key, _ = store
    us = retrieve.units(conn)
    assert len(us) > 30
    ref_nodes = {r[0] for r in conn.execute("SELECT node_id FROM refs WHERE node_id IS NOT NULL")}
    assert ref_nodes, "the fixture's reference list is read into refs"
    assert not ref_nodes & {u["node_id"] for u in us}
    assert all(u["role"] != "references" for u in us)
    assert all(u["type"] in ("paragraph", "list_item", "caption") for u in us)
    assert all(not u["ancestry"] or u["ancestry"][0] != "Front matter" for u in us)
    assert all(len(re.findall(r"[^\W\d_]{2,}", u["text"])) >= retrieve.MIN_WORDS for u in us)
    assert any(u["type"] == "caption" for u in us), "figures are found through their captions"
    assert {"methods", "results"} <= {u["role"] for u in us}
    t = retrieve.doc_text(us[0])
    assert t.startswith("search_document: ") and "\n" in t and us[0]["text"][:40] in t


def test_doc_text_caps_a_long_paragraph():
    t = retrieve.doc_text({"ancestry": json.dumps(["2. Methods", "2.1 Long"]), "text": "word " * 5000})
    assert t.startswith("search_document: 2. Methods > 2.1 Long\n")
    assert len(t) <= len("search_document: 2. Methods > 2.1 Long\n") + retrieve.DOC_CHARS


def test_fts_query_is_the_cli_tokeniser():
    assert retrieve.fts_query("What is the tensile strength of ELAC threads?") == '"tensile" OR "strength" OR "elac" OR "threads"'
    assert retrieve.fts_query("0.1% genipin, 4-h") == '"0.1%" OR "genipin" OR "4-h"'
    assert retrieve.fts_query("the of and") == ""


def test_embedding_is_idempotent(store):
    conn, _, _ = store
    e = BagEmbedder()
    seen = []
    first = retrieve.embed_library(conn, e, on_progress=lambda d, t: seen.append((d, t)), batch=16)
    n = len(retrieve.units(conn))
    assert first["units"] == n and first["embedded"] == n and first["already"] == 0 and "error" not in first
    assert seen[-1] == (n, n) and len(seen) == -(-n // 16)
    assert first["model"] == "bag@doc1"
    calls = e.calls
    again = retrieve.embed_library(conn, e)
    assert again["embedded"] == 0 and again["already"] == n and e.calls == calls
    row = conn.execute("SELECT dims, length(vec) FROM vectors LIMIT 1").fetchone()
    assert row[0] == e.dims and row[1] == 4 * e.dims
    # another embedder (or recipe) is another key: its own vectors, not these
    other = retrieve.embed_library(conn, BagEmbedder(dims=64, model="other"))
    assert other["embedded"] == n and conn.execute("SELECT COUNT(DISTINCT model) FROM vectors").fetchone()[0] == 2


def test_search_finds_a_distinctive_paragraph_through_words_and_meaning(store):
    conn, _, _ = store
    e = BagEmbedder()
    retrieve.embed_library(conn, e)
    us = retrieve.units(conn)
    target = next(u for u in us if "antigenicity" in u["text"].lower())

    hits = retrieve.search(conn, "antigenicity", e, k=5)
    assert hits.meaning == "ok"
    assert hits[0]["node_id"] == target["node_id"] and hits[0]["ranks"]["words"] == 1

    words = _unique_words(us, target)
    assert len(words) >= 3
    hits = retrieve.search(conn, " ".join(words), e, k=5)
    assert hits[0]["node_id"] == target["node_id"]
    assert hits[0]["ranks"] == {"words": 1, "meaning": 1}
    assert hits.counts["vectors"] == len(us)

    methods_only = retrieve.search(conn, " ".join(words), e, k=5, roles=["methods"])
    assert all(conn.execute("SELECT role FROM nodes WHERE node_id = ?", (h["node_id"],)).fetchone()[0] == "methods" for h in methods_only)


def test_hydrate_follows_measured_by_to_the_method(store):
    conn, _, _ = store
    src, dst, evidence = conn.execute(
        "SELECT e.src, e.dst, e.evidence FROM edges e JOIN nodes n ON n.node_id = e.src WHERE e.kind = 'measured_by' AND n.role = 'results' ORDER BY e.src LIMIT 1"
    ).fetchone()
    h = retrieve.hydrate(conn, src)
    assert h["hit"]["node_id"] == src and h["hit"]["role"] == "results"
    assert h["paper"]["key"] == "doi:10.3390/mi15070851"
    got = {m["node_id"]: m for m in h["methods"]}
    assert dst in got
    m = got[dst]
    assert m["via"] == "hit" and m["evidence"] == evidence and m["text"]
    dst_row = conn.execute("SELECT type, heading, role FROM nodes WHERE node_id = ?", (dst,)).fetchone()
    assert dst_row["role"] == "methods"
    if dst_row["type"] == "section":
        assert m["heading"] == dst_row["heading"] and m["ancestry"][-1] == dst_row["heading"]
    parent = conn.execute("SELECT parent FROM nodes WHERE node_id = ?", (src,)).fetchone()[0]
    assert h["section"]["node_id"] == parent
    # every method handed back is a row in edges, never a guess
    for m in h["methods"]:
        assert conn.execute("SELECT 1 FROM edges WHERE dst = ? AND kind = 'measured_by'", (m["node_id"],)).fetchone()


def test_hydrate_falls_back_to_the_sections_methods_and_says_so(store):
    conn, _, _ = store
    row = conn.execute(
        """SELECT n.node_id, n.parent FROM nodes n
           WHERE n.type = 'paragraph' AND n.role = 'results'
             AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.src = n.node_id AND e.kind = 'measured_by')
             AND EXISTS (SELECT 1 FROM edges e JOIN nodes s ON s.node_id = e.src WHERE s.parent = n.parent AND e.kind = 'measured_by')
           ORDER BY n.rowid LIMIT 1"""
    ).fetchone()
    if row is None:
        pytest.skip("every results paragraph in the fixture has its own edge")
    h = retrieve.hydrate(conn, row["node_id"])
    assert h["methods"] and all(m["via"] == "section" and m["sources"] for m in h["methods"])
    for m in h["methods"]:
        for s in m["sources"]:
            assert conn.execute("SELECT 1 FROM edges WHERE src = ? AND dst = ? AND kind = 'measured_by'", (s, m["node_id"])).fetchone()


def test_before_and_after_are_the_true_neighbours(store):
    conn, _, _ = store
    for u in retrieve.units(conn):
        if u["type"] != "paragraph":
            continue
        sibs = [r[0] for r in conn.execute(
            "SELECT node_id FROM nodes WHERE parent = (SELECT parent FROM nodes WHERE node_id = ?) AND type IN ('paragraph', 'list_item') AND text != '' ORDER BY ordinal", (u["node_id"],))]
        i = sibs.index(u["node_id"])
        if 0 < i < len(sibs) - 1:
            break
    else:
        pytest.fail("no paragraph with neighbours on both sides")
    h = retrieve.hydrate(conn, u["node_id"])
    assert [b["node_id"] for b in h["before"]] == [sibs[i - 1]]
    assert [a["node_id"] for a in h["after"]] == [sibs[i + 1]]
    wide = retrieve.hydrate(conn, u["node_id"], before=5, after=0)
    assert [b["node_id"] for b in wide["before"]] == sibs[max(0, i - 5) : i] and wide["after"] == []

    cap = next(u for u in retrieve.units(conn) if u["type"] == "caption")
    hc = retrieve.hydrate(conn, cap["node_id"])
    fig = conn.execute("SELECT parent FROM nodes WHERE node_id = ?", (cap["node_id"],)).fetchone()[0]
    assert hc["hit"]["figure"] == fig
    for n in (*hc["before"], *hc["after"]):
        assert conn.execute("SELECT parent FROM nodes WHERE node_id = ?", (n["node_id"],)).fetchone()[0] == conn.execute("SELECT parent FROM nodes WHERE node_id = ?", (fig,)).fetchone()[0]


def test_hydrate_carries_figures_and_citations_that_are_rows(store):
    conn, _, _ = store
    src = conn.execute("SELECT src FROM edges WHERE kind = 'cites_figure' ORDER BY src LIMIT 1").fetchone()[0]
    h = retrieve.hydrate(conn, src)
    assert h["figures"] and all(f["caption"] for f in h["figures"])
    cited = conn.execute("SELECT node_id FROM citations ORDER BY node_id LIMIT 1").fetchone()
    if cited:
        hc = retrieve.hydrate(conn, cited[0])
        assert hc["cites"] and all(c["ref_no"] for c in hc["cites"])
    assert retrieve.hydrate(conn, "nope")["error"]


def test_vectors_cascade_with_their_nodes_and_come_back(store):
    conn, key, tree = store
    e = BagEmbedder()
    first = retrieve.embed_library(conn, e)
    assert retrieve.search(conn, "antigenicity", e).counts["vectors"] == first["units"]
    save_tree(conn, key, tree, parser="test", parsed_at="t2", seconds=1.0)  # a rebuild
    assert conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0
    assert retrieve.search(conn, "antigenicity", e).meaning == "empty"
    again = retrieve.embed_library(conn, e)
    assert again["embedded"] == first["units"] and again["already"] == 0
    hits = retrieve.search(conn, "antigenicity", e)
    assert hits.meaning == "ok" and hits.counts["vectors"] == first["units"]
    conn.execute("DELETE FROM papers WHERE key = ?", (key,))
    assert conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0


def test_an_embedder_that_is_down_gives_words_only_and_says_so(store):
    conn, _, _ = store
    e = BagEmbedder()
    e.down = True
    r = retrieve.embed_library(conn, e)
    assert r["error"] and r["embedded"] == 0
    assert conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0

    e.down = False
    retrieve.embed_library(conn, e)
    e.down = True
    a = retrieve.query(conn, "antigenicity crosslinking", e, k=3)
    assert a["embedder"]["down"] is True and a["embedder"]["meaning"] == "down"
    assert a["hits"] and all(set(h["ranks"]) == {"words"} for h in a["hits"])
    json.dumps(a)


def test_query_hydrates_and_folds_neighbours_into_the_better_hit(store):
    conn, _, _ = store
    e = BagEmbedder()
    retrieve.embed_library(conn, e)
    us = retrieve.units(conn)
    by_id = {u["node_id"]: u for u in us}
    pair = None
    for u in us:
        if u["type"] != "paragraph":
            continue
        h = retrieve.hydrate(conn, u["node_id"])
        if h["after"] and h["after"][0]["node_id"] in by_id:
            pair = (u, by_id[h["after"][0]["node_id"]])
            wa, wb = _unique_words(us, pair[0], 4), _unique_words(us, pair[1], 4)
            if len(wa) >= 3 and len(wb) >= 3:
                break
    assert pair
    a = retrieve.query(conn, " ".join(wa + wb), e, k=4)
    ids = [h["hit"]["node_id"] for h in a["hits"]]
    assert len([x for x in ids if x in (pair[0]["node_id"], pair[1]["node_id"])]) == 1
    top = next(h for h in a["hits"] if h["hit"]["node_id"] in (pair[0]["node_id"], pair[1]["node_id"]))
    other = pair[1]["node_id"] if top["hit"]["node_id"] == pair[0]["node_id"] else pair[0]["node_id"]
    assert other in top["also"]
    assert a["counts"]["also"] >= 1
    assert [h["rank"] for h in a["hits"]] == list(range(1, len(a["hits"]) + 1))
    assert {"rank", "score", "ranks", "hit", "paper", "section", "before", "after", "methods", "figures", "cites"} <= set(a["hits"][0])
    assert all("also" not in h or h["also"] for h in a["hits"]), "`also` is there only when something folded"
    json.dumps(a)


def test_status_counts_what_is_embedded_without_embedding(store, monkeypatch):
    conn, _, _ = store
    e = BagEmbedder()
    n = len(retrieve.units(conn))
    s = retrieve.status(conn, e)  # before the vectors table exists
    assert s == {"units": n, "embedded": 0, "model": "bag@doc1", "down": False, "error": None}
    retrieve.embed_library(conn, e, batch=16)
    calls = e.calls
    assert retrieve.status(conn, e)["embedded"] == n and e.calls == calls, "status never embeds"
    assert retrieve.status(conn, BagEmbedder(model="other"))["embedded"] == 0

    class Tags:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self.body).encode()

    o = retrieve.OllamaEmbedder(url="http://127.0.0.1:9", model="nomic-embed-text")
    monkeypatch.setattr(retrieve.urllib.request, "urlopen", lambda url, timeout: Tags({"models": [{"name": "nomic-embed-text:latest"}]}))
    s = retrieve.status(conn, o)
    assert s["model"] == "nomic-embed-text@doc1" and s["down"] is False and s["embedded"] == 0 and s["units"] == n
    monkeypatch.setattr(retrieve.urllib.request, "urlopen", lambda url, timeout: Tags({"models": [{"name": "qwen3:14b"}]}))
    s = retrieve.status(conn, o)
    assert s["down"] is True and "nomic-embed-text" in s["error"]

    def refused(url, timeout):
        raise OSError("refused")

    monkeypatch.setattr(retrieve.urllib.request, "urlopen", refused)
    s = retrieve.status(conn, o)
    assert s["down"] is True and "refused" in s["error"]
    assert retrieve.status(conn, o, ping=False)["down"] is False
    json.dumps(s)


def test_ollama_retries_a_server_error_and_gives_up_at_once_when_nobody_is_there(monkeypatch):
    import urllib.error

    monkeypatch.setattr(retrieve.time, "sleep", lambda s: None)
    e = retrieve.OllamaEmbedder(url="http://127.0.0.1:9", model="m")
    tries = []

    def flaky(texts):
        tries.append(len(texts))
        if len(tries) == 1:
            raise urllib.error.HTTPError(e.url, 400, "Bad Request", {}, None)
        return [[1.0, 0.0]] * len(texts)

    monkeypatch.setattr(e, "_post", flaky)
    assert e.embed(["a", "b"]) == [[1.0, 0.0], [1.0, 0.0]] and tries == [2, 2] and e.error is None

    def refused(texts):
        tries.append(-1)
        raise urllib.error.URLError("refused")

    tries.clear()
    monkeypatch.setattr(e, "_post", refused)
    assert e.embed(["a"]) is None and tries == [-1] and "refused" in e.error


def test_the_command_line_answers_json_without_an_embedder(store, tmp_path, monkeypatch, capsys):
    conn, _, _ = store
    conn.close()
    monkeypatch.setattr(retrieve.OllamaEmbedder, "embed", lambda self, texts: None)
    assert retrieve.main(["--store", str(tmp_path / "store.sqlite"), "--query", "antigenicity", "--json"]) == 0
    a = json.loads(capsys.readouterr().out)
    assert a["hits"] and a["embedder"]["model"].endswith("@doc1")
    assert retrieve.main(["--store", str(tmp_path / "store.sqlite"), "--embed"]) == 1  # down: says so, writes nothing


# -- hydration: which method, which paragraph of it, and the edges walked the other way -------------------------------


def test_edges_are_ordered_by_their_evidence_and_by_how_much_of_it():
    from litrag_parser.edges import CAPTION_BAND, POINTER_SCORE, SIMILARITY_BAND, TERMS_BAND, banded, strength

    assert strength(["compressive modulus", "calcein"]) == 3  # a pair weighs two, a word one
    word, pair, many = banded(["calcein"], TERMS_BAND), banded(["compressive modulus"], TERMS_BAND), banded(["a b", "c d", "e f", "g"], TERMS_BAND)
    assert TERMS_BAND[0] < word < pair < many == TERMS_BAND[1] < POINTER_SCORE
    # every kind stays in its band: the strongest caption below the weakest mark, similarity below both
    assert banded(["a b", "c d", "e f", "g h"], CAPTION_BAND) == CAPTION_BAND[1] <= TERMS_BAND[0]
    assert SIMILARITY_BAND[1] <= CAPTION_BAND[0]


def test_edges_of_one_finding_come_strongest_first(store):
    conn, _, _ = store
    scores = [r[0] for r in conn.execute("SELECT score FROM edges WHERE kind = 'measured_by' AND evidence = 'terms'")]
    assert scores and all(0.80 < s <= 0.95 for s in scores) and len(set(scores)) > 1  # no longer one flat 0.9


def test_choose_paragraph_follows_the_marks_then_the_rarer_words():
    P = lambda i, t: {"node_id": f"p{i}", "text": t, "page": 1}  # noqa: E731
    paras = [
        P(1, "Collagen threads were electrocompacted between two electrodes at three volts."),
        P(2, "Compressive modulus of collagen samples was measured on an Instron with a load cell."),
        P(3, "Swelling ratio was computed from wet and dry weights of collagen threads."),
    ]
    p, why = retrieve.choose_paragraph(paras, "The compressive modulus rose twofold", ["compressive modulus"])
    assert p["node_id"] == "p2" and why[0] == "compressive modulus"
    p, why = retrieve.choose_paragraph(paras, "The swelling ratio of the threads fell after crosslinking", [])
    assert p["node_id"] == "p3" and "swelling ratio" in why
    # a word every paragraph says tells them apart from nothing: the method is given whole
    assert retrieve.choose_paragraph(paras, "Collagen was the material", []) == (None, [])
    assert retrieve.choose_paragraph(paras[:1], "anything at all", [])[0]["node_id"] == "p1"


def test_a_finding_is_given_the_paragraph_of_its_method_it_rests_on(store):
    conn, _, _ = store
    for src, dst, detail in conn.execute("SELECT src, dst, detail FROM edges WHERE kind = 'measured_by' AND evidence = 'terms' ORDER BY score DESC, src").fetchall():
        paras = {p["node_id"]: p["text"] for p in retrieve._paragraphs_under(conn, dst)}
        if len(paras) > 1:
            break
    else:
        pytest.skip("no finding in the fixture is linked to a method of several paragraphs")
    h = retrieve.hydrate(conn, src)
    m = next(m for m in h["methods"] if m["node_id"] == dst)
    assert m["paragraph"] in paras and m["paragraphs"] == len(paras)
    assert m["text"] == retrieve._cut(paras[m["paragraph"]], retrieve.METHOD_CHARS) and m["chars"] > len(paras[m["paragraph"]])
    from litrag_parser.edges import _terms

    assert m["matched"] and all(t in _terms(paras[m["paragraph"]]) for t in m["matched"])
    assert retrieve.best_paragraph(conn, src, dst) == m["paragraph"]  # what the link labels are measured against


def test_statistics_and_materials_are_shown_apart_and_never_first(store):
    conn, key, _ = store
    stats = conn.execute("SELECT node_id FROM nodes WHERE canonical = 'Statistical analysis'").fetchone()[0]
    src, dst = conn.execute("SELECT src, dst FROM edges WHERE kind = 'measured_by' ORDER BY score DESC, src LIMIT 1").fetchone()
    conn.execute("INSERT INTO edges(paper, src, dst, kind, evidence, detail, score) VALUES (?,?,?,?,?,?,?)",
                 (key, src, stats, "measured_by", "pointer", "Section 2.7", 1.0))  # stronger than any edge it has
    h = retrieve.hydrate(conn, src)
    assert [g["node_id"] for g in h["general"]] == [stats] and h["general"][0]["general"]
    assert stats not in [m["node_id"] for m in h["methods"]] and dst in [m["node_id"] for m in h["methods"]]


def test_a_finding_linked_only_to_statistics_still_gets_its_sections_methods(store):
    conn, key, _ = store
    row = conn.execute(
        """SELECT n.node_id FROM nodes n
           WHERE n.type = 'paragraph' AND n.role = 'results'
             AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.src = n.node_id AND e.kind = 'measured_by')
             AND EXISTS (SELECT 1 FROM edges e JOIN nodes s ON s.node_id = e.src WHERE s.parent = n.parent AND e.kind = 'measured_by')
           ORDER BY n.rowid LIMIT 1"""
    ).fetchone()
    if row is None:
        pytest.skip("every results paragraph in the fixture has its own edge")
    stats = conn.execute("SELECT node_id FROM nodes WHERE canonical = 'Statistical analysis'").fetchone()[0]
    conn.execute("INSERT INTO edges(paper, src, dst, kind, evidence, detail, score) VALUES (?,?,?,?,?,?,?)",
                 (key, row["node_id"], stats, "measured_by", "terms", "statistical significance", 0.9))
    h = retrieve.hydrate(conn, row["node_id"])
    assert [g["node_id"] for g in h["general"]] == [stats]
    assert h["methods"] and all(m["via"] == "section" for m in h["methods"])


def test_a_methods_hit_lists_the_findings_its_method_measured(store):
    conn, _, _ = store
    dst, total = conn.execute("SELECT dst, count(*) FROM edges WHERE kind = 'measured_by' GROUP BY dst ORDER BY count(*) DESC, dst LIMIT 1").fetchone()
    para = retrieve._paragraphs_under(conn, dst)[0]["node_id"]
    h = retrieve.hydrate(conn, para)
    f = h["findings"]
    assert f["method"] == dst and f["total"] == total and len(f["findings"]) == min(total, retrieve.FINDINGS_SHOWN)
    scores = [x["score"] for x in f["findings"]]
    assert scores == sorted(scores, reverse=True)
    for x in f["findings"]:
        assert conn.execute("SELECT 1 FROM edges WHERE src = ? AND dst = ? AND kind = 'measured_by'", (x["node_id"], dst)).fetchone()
    src = conn.execute("SELECT src FROM edges WHERE kind = 'measured_by' LIMIT 1").fetchone()[0]
    assert retrieve.hydrate(conn, src)["findings"] is None  # a finding is not asked the other way round
