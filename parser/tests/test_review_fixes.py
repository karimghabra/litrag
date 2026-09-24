"""What an independent review of the studio found (2026-09-23), each pinned by a test: a cached
matrix that outlived its vectors, reference entries searched by meaning, a merge that passed a
failed paper off as read and crashed on an older library's searches, a rename that captured
another project's requests, and manifest writes that lost each other's changes."""

import json
import shutil
import threading
from pathlib import Path

from litrag_parser import projects, retrieve
from litrag_parser.library import create_library, open_library, queries_of, update_manifest
from litrag_parser.store import file_paper, open_store

FIXTURES = Path(__file__).parent / "fixtures"
TEXT = "the hydrogel was tested under cyclic compression loading for fourteen days"


class Fake:
    model = "fake"

    def __init__(self, v):
        self.v = v

    def embed(self, texts):
        return [list(self.v) for _ in texts]


def _put(conn, key, ids):
    with conn:
        conn.execute("DELETE FROM nodes WHERE paper = ?", (key,))  # what save_tree does
        conn.execute("INSERT OR IGNORE INTO papers(key, title, status, added_at) VALUES (?,?,?,?)", (key, key, "parsed", "now"))
        conn.execute("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, ancestry, text) VALUES (?,?,?,?,?,?,?,?,?,?)", (key, key, None, 0, 0, "document", "document", "other", "[]", ""))
        for i, nid in enumerate(ids):
            conn.execute("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, ancestry, text) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (nid, key, key, i, 1, "paragraph", "text", "results", json.dumps(["Results"]), TEXT))


def test_the_matrix_follows_a_rebuild_of_the_last_paper_embedded(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    _put(conn, "A", ["A/1", "A/2"])
    _put(conn, "B", ["B/1", "B/2"])
    retrieve.embed_library(conn, Fake([1.0, 0.0]))
    key = retrieve.model_key(Fake([1.0, 0.0]))
    first = retrieve._matrix(conn, key)
    assert first["ids"][-2:] == ["B/1", "B/2"]
    _put(conn, "B", ["B/x", "B/y"])  # the same count again, and SQLite hands the freed rowids out again
    retrieve.embed_library(conn, Fake([0.0, 1.0]), paper="B")
    again = retrieve._matrix(conn, key)
    assert again is not first and again["ids"][-2:] == ["B/x", "B/y"] and list(again["matrix"][-1]) == [0.0, 1.0]


def test_a_reference_entry_is_never_a_hit_by_meaning(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    _put(conn, "A", ["A/1", "A/2"])
    retrieve.embed_library(conn, Fake([1.0, 0.0]))  # before the reference list was linked: both embedded
    with conn:
        conn.execute("INSERT INTO refs(paper, ref_no, node_id, text) VALUES ('A', 1, 'A/2', ?)", (TEXT,))
    m = retrieve._matrix(conn, retrieve.model_key(Fake([1.0, 0.0])))
    assert m["ids"] == ["A/1"]


def _paper_lib(root, name, status="parsed", error=None):
    lib = create_library(root, name)
    conn = open_store(lib.store_path)
    key = file_paper(conn, title="x", file="", sha256=name, fmt="jats", doi=f"10.1/{name}", pmid=None, pmcid=None, now="t").key
    shutil.copy(FIXTURES / "PMC11278924.xml", lib.papers_dir / "p.xml")
    shutil.copy(FIXTURES / "PMC11278924.jats.docling.json", lib.parsed_dir / f"{key.replace(':', '_').replace('/', '_')}.docling.json")
    conn.execute("UPDATE papers SET file = 'p.xml', status = ?, error = ? WHERE key = ?", (status, error, key))
    conn.commit()
    conn.close()
    return lib, key


def test_a_merge_keeps_a_failed_paper_failed_to_be_read_again_and_takes_old_searches(tmp_path):
    a, ka = _paper_lib(tmp_path, "one", status="failed", error="build_tree raised")
    b, kb = _paper_lib(tmp_path, "two")
    for lib in (a, b):  # a library from before the studio: its searches are bare strings
        update_manifest(lib, lambda m: m.update(queries=["collagen AND tendon"]))
    out = projects.merge(tmp_path, ["one", "two"], "Both")
    target = open_library(tmp_path, "both")
    conn = open_store(target.store_path)
    rows = {r["key"]: dict(r) for r in conn.execute("SELECT key, status, error FROM papers")}
    assert rows[ka]["status"] == "queued" and rows[ka]["error"] == "build_tree raised"
    assert rows[kb]["status"] == "parsed"
    assert ka in out["keys_to_parse"] and kb in out["keys_to_rebuild"]
    assert [q["query"] for q in queries_of(json.loads(target.manifest_path.read_text("utf-8")))] == ["collagen AND tendon"]


def test_a_rename_cannot_capture_another_projects_requests(tmp_path):
    create_library(tmp_path, "Tendon")
    cart = create_library(tmp_path, "Cartilage")
    try:
        projects.describe(cart, name="tendon")
        raise AssertionError("renamed onto another project's id")
    except ValueError:
        pass
    assert open_library(tmp_path, "tendon").id == "tendon"


def test_manifest_writes_from_three_threads_all_land(tmp_path):
    lib = create_library(tmp_path, "Busy")

    def add(i):
        for j in range(15):
            update_manifest(lib, lambda m, i=i, j=j: m.setdefault("queries", []).append({"query": f"q{i}-{j}"}))

    threads = [threading.Thread(target=add, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = json.loads(lib.manifest_path.read_text("utf-8"))["queries"]
    assert len(got) == 45 and not list(lib.dir.glob("*.part"))
