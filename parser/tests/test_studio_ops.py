"""The studio's ops over the worker's own wire: projects with their descriptions, candidates, the
types and a paper's mapping — answered as JSON, and a bad request answered as an error."""

import json
import shutil

from test_worker import FIXTURES, by, talk


def test_projects_describe_candidates_types_and_mapping(tmp_path):
    events = talk(tmp_path, [
        {"id": "1", "op": "init", "name": "Looped Ligament", "description": "Aligned collagen threads for tendon repair."},
        {"id": "2", "op": "projects"},
        {"id": "3", "op": "describe", "lib": "looped-ligament", "description": "Electrochemically aligned collagen."},
        {"id": "4", "op": "candidates", "lib": "looped-ligament"},
        {"id": "5", "op": "types", "lib": "looped-ligament"},
        {"id": "6", "op": "mapping", "lib": "looped-ligament", "key": "doi:nothing"},
        {"id": "7", "op": "describe", "lib": "no-such-project", "description": "x"},
        {"id": "8", "op": "merge", "sources": ["looped-ligament"], "name": "Merged"},
    ])
    assert by(events, "1")[0]["library"]["id"] == "looped-ligament"
    projects = by(events, "2")[0]["projects"]
    assert [p["id"] for p in projects] == ["looped-ligament"]
    assert projects[0]["description"] == "Aligned collagen threads for tendon repair."
    assert projects[0]["counts"]["papers"] == 0
    assert by(events, "3")[0]["project"]["description"] == "Electrochemically aligned collagen."
    assert by(events, "4")[0]["candidates"] == []
    types = by(events, "5")[0]
    assert types["event"] == "types" and types["overview"] == [] and types["papers"] == []
    assert by(events, "6")[0]["event"] in ("error", "mapping")
    assert by(events, "7")[0]["event"] == "error"
    assert by(events, "8")[0]["event"] == "queued"
    manifest = json.loads((tmp_path / "looped-ligament" / "library.json").read_text("utf-8"))
    assert manifest["description"] == "Electrochemically aligned collagen."


def test_types_and_mapping_of_a_read_paper(tmp_path):
    """A paper read from its saved Docling document (a rebuild, no Docling): its type's structure
    and its mapping onto it come back over the wire."""
    lib = tmp_path / "micromachines"
    events = talk(tmp_path, [{"id": "1", "op": "init", "name": "Micromachines"}])
    assert by(events, "1")[0]["event"] == "library"
    import sqlite3

    from litrag_parser.library import safe_key
    from litrag_parser.store import file_paper, open_store

    conn = open_store(lib / "store.sqlite")
    key = file_paper(conn, title="x", file="", sha256="aa", fmt="jats", doi="10.3390/mi15070851", pmid=None, pmcid=None, now="t").key
    shutil.copy(FIXTURES / "PMC11278924.xml", lib / "papers" / "paper.xml")
    shutil.copy(FIXTURES / "PMC11278924.jats.docling.json", lib / "parsed" / f"{safe_key(key)}.docling.json")
    conn.execute("UPDATE papers SET file = 'paper.xml', status = 'parsed' WHERE key = ?", (key,))
    conn.commit()
    conn.close()
    from litrag_parser.worker import Worker

    Worker(tmp_path).do_rebuild({"id": "r", "op": "rebuild", "lib": "micromachines"})  # what the ingest thread does, here in the test
    events = talk(tmp_path, [
        {"id": "t", "op": "types", "lib": "micromachines"},
        {"id": "m", "op": "mapping", "lib": "micromachines", "key": key},
    ])
    types = by(events, "t")[0]
    assert [t["type"] for t in types["overview"]] == ["research"]
    slots = [s["slot"] for s in types["skeletons"]["research"]["slots"]]
    assert slots[:5] == ["abstract", "introduction", "methods", "results", "discussion"]
    m = by(events, "m")[0]
    assert m["mapping"]["paper"]["key"] == key
    assert {s["slot"] for s in m["mapping"]["slots"] if s["status"] == "matched"} >= {"abstract", "introduction", "methods", "results", "discussion"}
    order = [s["slot"] for s in m["canonical"]["slots"] if s["sections"]]
    core = ["abstract", "introduction", "methods", "results", "discussion"]
    assert [x for x in order if x in core] == core and order[-1] in ("references", "back")  # the canonical face in the type's order, furniture last
    assert sqlite3.connect(lib / "store.sqlite").execute("select count(*) from nodes").fetchone()[0] > 50
