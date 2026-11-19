"""The shared store (members.py, migrate.py): every paper stored once by its identifier, a project
the list of papers it holds, every read the worker answers for a project narrowed to them, and the
libraries of the old shape moved across without losing a row — a dry run first, a backup, the
sources untouched, and twice the same as once."""

import json
import sqlite3
from pathlib import Path

import pytest

from litrag_parser import members, migrate, projects, retrieve
from litrag_parser.library import ALL, create_library, list_libraries, open_library, safe_key, shared_dir
from litrag_parser.store import open_store

from test_projects import DOC, SHARED, _add, _digest, two  # noqa: F401 — `two` is the fixture: two old libraries sharing a paper
from test_worker import FIXTURES, by, talk


def _count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def _shared_project(root: Path, name: str):
    lib = create_library(root, name, shared=True)
    lib.ensure_dirs()
    return lib


def _hold(lib, conn, doi: str, source_name: str):
    """A paper filed in the shared store as an ingest files it, and held by `lib`."""
    src = lib.root / f"{source_name}.pdf"
    src.write_bytes(f"%PDF-1.4 {source_name}".encode())
    key = _add(lib, conn, doi=doi, source=src, fmt="pdf")
    members.add(conn, lib, [key])
    src.unlink()
    return key


# ---------------------------------------------------------------- one store, many projects


def test_a_paper_two_projects_hold_is_stored_once_and_each_reads_only_its_own(tmp_path):
    a = _shared_project(tmp_path, "Tendon")
    b = _shared_project(tmp_path, "Bone")
    assert a.store_path == b.store_path == shared_dir(tmp_path) / "store.sqlite"
    assert a.inbox_dir == tmp_path / "tendon" / "inbox"  # the inbox stays the project's own
    conn = open_store(a.store_path)
    k1 = _hold(a, conn, "10.1/one", "one")
    k2 = _hold(b, conn, "10.1/two", "two")
    members.add(conn, b, [k1])  # Bone takes Tendon's paper: the same row, not a copy
    assert _count(conn, "SELECT COUNT(*) FROM papers") == 2
    assert members.scope(conn, a) == [k1] and set(members.scope(conn, b)) == {k1, k2}
    assert members.projects_of(conn, k1) == ["bone", "tendon"]
    assert members.scope(conn, open_library(tmp_path, ALL)) is None  # every paper: no narrowing
    members.remove(conn, b, [k1])
    assert members.scope(conn, b) == [k2] and _count(conn, "SELECT COUNT(*) FROM nodes WHERE paper = ?", k1) > 0  # let go, not deleted
    conn.close()

    events = talk(tmp_path, [
        {"id": "a", "op": "papers", "lib": "tendon"},
        {"id": "b", "op": "papers", "lib": "bone"},
        {"id": "all", "op": "papers", "lib": "*"},
        {"id": "p", "op": "projects"},
        {"id": "h", "op": "hold", "lib": "bone", "keys": [k1]},
        {"id": "b2", "op": "papers", "lib": "bone"},
        {"id": "x", "op": "hold", "lib": "bone", "keys": ["doi:10.9/not-held"]},
        {"id": "w", "op": "ingest", "lib": "*", "paths": []},
    ])
    assert [p["key"] for p in by(events, "a")[0]["papers"]] == [k1]
    assert [p["key"] for p in by(events, "b")[0]["papers"]] == [k2]
    assert {p["key"] for p in by(events, "all")[0]["papers"]} == {k1, k2}
    counts = {p["id"]: p["counts"] for p in by(events, "p")[0]["projects"]}
    assert counts["tendon"]["papers"] == 1 and counts["bone"]["papers"] == 1 and counts["tendon"]["nodes"] > 0
    assert by(events, "h")[0]["changed"] == 1 and {p["key"] for p in by(events, "b2")[0]["papers"]} == {k1, k2}
    assert by(events, "x")[0]["event"] == "error"  # a project holds papers the store has, nothing else
    assert by(events, "w")[0]["event"] == "error"  # nothing is filed into every project at once


def test_a_search_is_narrowed_to_a_project_or_to_several(tmp_path):
    a = _shared_project(tmp_path, "Tendon")
    b = _shared_project(tmp_path, "Bone")
    conn = open_store(a.store_path)
    k1 = _hold(a, conn, "10.1/one", "one")
    k2 = _hold(b, conn, "10.1/two", "two")  # the same document under another key: the same words in both
    question = "electrochemically aligned collagen"
    every = {retrieve.search(conn, question, None, k=50)[i]["node_id"].split("#")[0] for i in range(len(retrieve.search(conn, question, None, k=50)))}
    assert every == {k1, k2}
    only_a = {h["node_id"].split("#")[0] for h in retrieve.search(conn, question, None, k=50, paper=members.scope(conn, a))}
    assert only_a == {k1}
    both = members.scope_of(conn, [a, b])
    assert {h["node_id"].split("#")[0] for h in retrieve.search(conn, question, None, k=50, paper=both)} == {k1, k2}
    assert list(retrieve.search(conn, question, None, k=50, paper=[])) == []  # a project holding nothing finds nothing
    conn.close()
    old = create_library(tmp_path, "Old shape")
    with pytest.raises(ValueError, match="own store"):
        members.scope_of(open_store(a.store_path), [a, old])


def test_a_project_s_candidates_are_its_searches_and_what_its_papers_cite(tmp_path):
    from litrag_parser import acquire

    a = _shared_project(tmp_path, "Tendon")
    b = _shared_project(tmp_path, "Bone")
    conn = open_store(a.store_path)
    acquire.ensure_schema(conn)
    k1 = _hold(a, conn, "10.1/one", "one")
    searched, _ = acquire.upsert_candidate(conn, {"doi": "10.9/searched", "title": "Searched by Bone"}, query="q", now="t")
    cited, _ = acquire.upsert_candidate(conn, {"doi": "10.9/cited", "title": "Cited by Tendon's paper"}, query=None, now="t", round=2)
    members.add_candidates(conn, b, [searched])
    from litrag_parser import graph

    graph.ensure_schema(conn)
    conn.execute("INSERT INTO cites VALUES (?, ?, 'refs', 1)", (k1, f"cand:{cited}"))
    conn.commit()
    assert members.candidate_ids(conn, a) == {cited}
    assert members.candidate_ids(conn, b) == {searched}
    conn.close()
    events = talk(tmp_path, [{"id": "a", "op": "candidates", "lib": "tendon"}, {"id": "b", "op": "candidates", "lib": "bone"}])
    assert [c["cand_id"] for c in by(events, "a")[0]["candidates"]] == [cited]
    assert [c["cand_id"] for c in by(events, "b")[0]["candidates"]] == [searched]


def test_projects_of_the_shared_store_merge_by_holding_and_delete_by_letting_go(tmp_path):
    a = _shared_project(tmp_path, "Tendon")
    b = _shared_project(tmp_path, "Bone")
    conn = open_store(a.store_path)
    k1 = _hold(a, conn, "10.1/one", "one")
    k2 = _hold(b, conn, "10.1/two", "two")
    members.add(conn, b, [k1])
    conn.close()
    out = projects.merge(tmp_path, ["tendon", "bone"], "Both")
    assert out["target"]["store"] == "shared" and out["filed"] == 2 and out["keys_to_rebuild"] == [] and out["keys_to_parse"] == []
    conn = open_store(a.store_path)
    assert set(members.scope(conn, open_library(tmp_path, "both"))) == {k1, k2} and _count(conn, "SELECT COUNT(*) FROM papers") == 2
    conn.close()
    gone = projects.delete(open_library(tmp_path, "tendon"))
    assert gone["papers"] == 1 and Path(gone["aside"]).joinpath("library.json").exists()
    assert open_library(tmp_path, "tendon") is None and [l.id for l in list_libraries(tmp_path)] == ["bone", "both"]
    conn = open_store(a.store_path)
    assert _count(conn, "SELECT COUNT(*) FROM papers") == 2 and set(members.scope(conn, open_library(tmp_path, "bone"))) == {k1, k2}
    conn.close()
    with pytest.raises(ValueError, match="own store"):
        projects.delete(create_library(tmp_path, "Old shape"))


# ---------------------------------------------------------------- moving the old libraries across


def _rows(conn, table: str, paper: str | None = None) -> int:
    return _count(conn, f"SELECT COUNT(*) FROM {table}" + (" WHERE paper = ?" if paper else ""), *((paper,) if paper else ()))


def _with_vectors_and_labels(lib, key: str) -> int:
    """Vectors for a paper's passages and a person's label on it, as an embedded, labelled library holds them."""
    conn = open_store(lib.store_path)
    retrieve.ensure_schema(conn)
    nodes = [r[0] for r in conn.execute("SELECT node_id FROM nodes WHERE paper = ? AND type = 'paragraph' LIMIT 5", (key,))]
    for n in nodes:
        conn.execute("INSERT INTO vectors(node, model, dims, vec) VALUES (?, 'm', 2, ?)", (n, b"\x00" * 8))
    conn.execute("INSERT INTO link_labels(paper, finding, finding_text, method, verdict, by, at) VALUES (?, ?, 'f', '', 'none', 'person', 't')", (key, nodes[0]))
    conn.commit()
    conn.close()
    return len(nodes)


def test_the_dry_run_says_what_would_move_and_writes_nothing(two):
    root = two["root"]
    before = {lib: _digest(two[lib]) for lib in ("a", "b")}
    out = migrate.migrate(root, dry_run=True)
    assert out["dry_run"] and out["backup"] is None and out["moved"] == []
    by_id = {e["id"]: e for e in out["plan"]["libraries"]}
    assert by_id["collagen-a"]["papers"] == 1 and by_id["collagen-a"]["new"] == 1
    assert by_id["collagen-b"]["papers"] == 3 and by_id["collagen-b"]["held"] == 1 and by_id["collagen-b"]["new"] == 2  # the shared DOI: held by then
    assert out["plan"]["totals"]["new"] == 3
    assert not shared_dir(root).exists() and not (root / "_backups").exists()
    assert {lib: _digest(two[lib]) for lib in ("a", "b")} == before
    assert all(not l.shared for l in list_libraries(root))


def test_old_libraries_move_into_the_shared_store_with_every_row_and_file(two):
    root, a, b, ka = two["root"], two["a"], two["b"], two["ka"]
    vecs = _with_vectors_and_labels(a, ka)
    ca = sqlite3.connect(a.store_path)
    nodes_a, refs_a, events_a = _rows(ca, "nodes", ka), _rows(ca, "refs", ka), _rows(ca, "events", ka)
    ca.close()
    before = {lib: _digest(two[lib]) for lib in ("a", "b")}
    out = migrate.migrate(root)
    backup = Path(out["backup"])
    assert (backup / "collagen-a" / "store.sqlite").exists() and (backup / "collagen-b" / "library.json").exists()
    assert {m["id"]: (m["new"], m["held"]) for m in out["moved"]} == {"collagen-a": (1, 0), "collagen-b": (2, 1)}
    # the sources are as they were, but for the manifest that says where the project lives now
    for lib in ("a", "b"):
        after = _digest(two[lib])
        assert {k: v for k, v in after.items() if k != "library.json"} == {k: v for k, v in before[lib].items() if k != "library.json"}
    libs = {l.id: l for l in list_libraries(root)}
    assert libs["collagen-a"].shared and libs["collagen-b"].shared
    assert json.loads(a.manifest_path.read_text())["migratedFrom"] == str(a.store_path)

    conn = open_store(shared_dir(root) / "store.sqlite")
    assert _rows(conn, "papers") == 3  # the paper both held, once
    assert _rows(conn, "nodes", ka) == nodes_a and _rows(conn, "refs", ka) == refs_a
    assert _rows(conn, "events", ka) == events_a + 1  # its history, and the move
    assert _count(conn, "SELECT COUNT(*) FROM vectors v JOIN nodes n ON n.node_id = v.node WHERE n.paper = ?", ka) == vecs
    assert _count(conn, "SELECT COUNT(*) FROM nodes_fts WHERE nodes_fts MATCH 'collagen'") > 0  # words searchable at once
    assert _rows(conn, "link_labels", ka) == 1 and _rows(conn, "outlines", ka) == 1 and _rows(conn, "judgments", ka) == 1
    assert (shared_dir(root) / "parsed" / f"{safe_key(ka)}.docling.json").exists()
    file_a = conn.execute("SELECT file FROM papers WHERE key = ?", (ka,)).fetchone()[0]
    assert (shared_dir(root) / "papers" / file_a).exists()
    assert set(members.scope(conn, libs["collagen-a"])) == {ka}
    assert set(members.scope(conn, libs["collagen-b"])) == {ka, two["kb_other"], two["kb_unread"]}  # it holds the paper as read in A
    # candidates once by identifier, each project keeping its own, the dismissal kept
    assert _count(conn, "SELECT COUNT(*) FROM candidates WHERE lower(doi) = lower(?)", SHARED) == 1
    assert conn.execute("SELECT status FROM candidates WHERE doi = '10.9/only-b'").fetchone()[0] == "dismissed"
    ids_a, ids_b = members.candidate_ids(conn, libs["collagen-a"]), members.candidate_ids(conn, libs["collagen-b"])
    doi_of = dict(conn.execute("SELECT cand_id, doi FROM candidates"))
    assert {doi_of[i] for i in ids_a} == {SHARED, "10.9/only-a"} and "10.9/only-b" in {doi_of[i] for i in ids_b}
    counts = {t: _rows(conn, t) for t in ("papers", "nodes", "vectors", "candidates", "project_papers", "events")}
    conn.close()

    again = migrate.migrate(root)  # twice is once: nothing of the old shape is left
    assert again["moved"] == [] and again["backup"] is None
    conn = open_store(shared_dir(root) / "store.sqlite")
    assert {t: _rows(conn, t) for t in counts} == counts
    conn.close()


def test_a_move_interrupted_half_way_runs_again_to_the_same_end(two):
    root, a = two["root"], two["a"]
    migrate.migrate(root, only=["collagen-a"])
    # as if the move of A had been cut short before its manifest said so
    projects.update_manifest(open_library(root, "collagen-a"), lambda m: m.pop("store"))
    out = migrate.migrate(root)
    assert {m["id"]: (m["new"], m["held"]) for m in out["moved"]} == {"collagen-a": (0, 1), "collagen-b": (2, 1)}
    conn = open_store(shared_dir(root) / "store.sqlite")
    assert _rows(conn, "papers") == 3 and _count(conn, "SELECT COUNT(*) FROM nodes WHERE paper = ?", two["ka"]) == _count(
        sqlite3.connect(a.store_path), "SELECT COUNT(*) FROM nodes WHERE paper = ?", two["ka"])
    conn.close()


def test_a_merge_of_old_libraries_moves_them_first_and_reads_nothing_again(two):
    root = two["root"]
    out = projects.merge(root, ["collagen-a", "collagen-b"], "Collagen")
    assert out["target"]["store"] == "shared" and {m["id"] for m in out["migrated"]} == {"collagen-a", "collagen-b"}
    assert out["keys_to_rebuild"] == [] and out["keys_to_parse"] == [] and out["filed"] == 3
    assert all(l.shared for l in list_libraries(root))


def test_the_worker_moves_old_libraries_on_asking_and_says_which_are_left(two):
    root = two["root"]
    events = talk(root, [{"id": "p", "op": "projects"}, {"id": "d", "op": "migrate", "dry_run": True}])
    assert set(by(events, "p")[0]["own_store"]) == {"collagen-a", "collagen-b"}
    assert by(events, "d")[0]["plan"]["totals"]["new"] == 3 and not shared_dir(root).exists()
    from litrag_parser.worker import Worker

    seen = []
    import litrag_parser.worker as w

    keep = w.emit
    w.emit = seen.append
    try:
        Worker(root).do_migrate({"id": "m", "op": "migrate"})
    finally:
        w.emit = keep
    done = [e for e in seen if e.get("event") == "done"][0]
    assert done["op"] == "migrate" and len(done["moved"]) == 2 and Path(done["backup"]).is_dir()
    assert any(e.get("event") == "progress" for e in seen)
    events = talk(root, [{"id": "p", "op": "projects"}, {"id": "q", "op": "papers", "lib": "collagen-b"}])
    assert by(events, "p")[0]["own_store"] == []
    assert len(by(events, "q")[0]["papers"]) == 3


def test_a_query_names_its_projects_and_refuses_to_mix_stores(tmp_path, monkeypatch):
    import time

    from litrag_parser.worker import Worker

    monkeypatch.setenv("LITRAG_OLLAMA_URL", "http://127.0.0.1:9")  # no embedder: the words alone
    a = _shared_project(tmp_path, "Tendon")
    conn = open_store(a.store_path)
    _hold(a, conn, "10.1/one", "one")
    conn.close()
    create_library(tmp_path, "Old shape")
    seen: list[dict] = []
    monkeypatch.setattr("litrag_parser.worker.emit", seen.append)
    w = Worker(tmp_path)
    for req in ({"id": "q", "op": "query", "lib": "tendon", "question": "collagen", "k": 2},
                {"id": "all", "op": "query", "libs": ["*"], "question": "collagen", "k": 2},
                {"id": "mix", "op": "query", "libs": ["tendon", "old-shape"], "question": "collagen"}):
        w.answer(req)
    deadline = time.time() + 60
    while time.time() < deadline and len({e.get("id") for e in seen} & {"q", "all", "mix"}) < 3:
        time.sleep(0.05)
    assert by(seen, "q")[0]["scope"] == ["tendon"] and by(seen, "q")[0]["hits"]
    assert by(seen, "all")[0]["scope"] == ["*"]
    assert by(seen, "mix")[0]["event"] == "error" and "own store" in by(seen, "mix")[0]["message"]
