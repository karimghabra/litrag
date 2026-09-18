"""The worker over its own wire, without Docling's models: hello, init, parse_json, sql."""

import json
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def talk(root: Path, requests: list[dict]) -> list[dict]:
    lines = "".join(json.dumps(r) + "\n" for r in requests + [{"id": "q", "op": "quit"}])
    proc = subprocess.run([sys.executable, "-m", "litrag_parser.worker", f"--root={root}"], input=lines, capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=Path(__file__).parent.parent)
    assert proc.returncode == 0, proc.stderr
    return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]


def by(events, req_id):
    return [e for e in events if e.get("id") == req_id]


def test_hello_init_and_parse_json(tmp_path):
    events = talk(tmp_path, [
        {"id": "1", "op": "hello"},
        {"id": "2", "op": "init", "name": "Looped Ligament"},
        {"id": "3", "op": "libraries"},
        {"id": "4", "op": "parse_json", "path": str(FIXTURES / "PMC3258128.docling.json"), "key": "k"},
        {"id": "5", "op": "papers", "lib": "looped-ligament"},
        {"id": "6", "op": "sql", "lib": "Looped Ligament", "sql": "select count(*) c from papers"},
        {"id": "7", "op": "tree", "lib": "looped-ligament", "key": "nope"},
        {"id": "8", "op": "bogus"},
    ])
    assert events[0]["event"] == "ready"
    hello = by(events, "1")[0]
    assert hello["event"] == "hello" and hello["docling"] is None  # not imported until a paper needs it
    lib = by(events, "2")[0]["library"]
    assert lib["id"] == "looped-ligament" and (tmp_path / "looped-ligament" / "library.json").exists()
    assert (tmp_path / "looped-ligament" / "store.sqlite").exists()
    assert [l["id"] for l in by(events, "3")[0]["libraries"]] == ["looped-ligament"]
    tree = by(events, "4")[0]
    assert tree["event"] == "tree" and tree["has_methods"] and tree["roles"]["methods"] == 23
    assert by(events, "5")[0]["papers"] == []
    assert by(events, "6")[0]["rows"] == [{"c": 0}]
    assert by(events, "7")[0]["event"] == "error"
    assert by(events, "8")[0]["event"] == "error"
    assert events[-1]["event"] == "bye"


def test_a_papers_own_doi_is_picked_over_its_datasets():
    from litrag_parser.worker import pick_doi

    page = "HardwareX 11 (2022) e00297 Design files: https://doi.org/10.5281/zenodo.4996271 Continuous fiber extruder https://doi.org/10.1016/j.ohx.2022.e00297 Received 1 March"
    assert pick_doi(page) == "10.1016/j.ohx.2022.e00297"  # the dataset's Zenodo DOI stands first on the page and is not the paper's
    assert pick_doi("A technical report. https://doi.org/10.5281/zenodo.4897976.") == "10.5281/zenodo.4897976"  # all there is: a report filed there
    assert pick_doi("PNAS 2026 https://doi.org/10.1073/pnas and then 10.1073/pnas.2601235123") == "10.1073/pnas.2601235123"
    assert pick_doi("no identifier on this page") is None


def test_audit_op_over_a_raw_document(tmp_path):
    events = talk(tmp_path, [
        {"id": "1", "op": "audit", "path": str(FIXTURES / "PMC11278924.jats.docling.json"), "key": "doi:10.3390/mi15070851"},
        {"id": "2", "op": "init", "name": "Empty"},
        {"id": "3", "op": "audit", "lib": "empty"},
    ])
    a = by(events, "1")[0]
    assert a["event"] == "audit" and [p["key"] for p in a["papers"]] == ["doi:10.3390/mi15070851"]
    paper = a["papers"][0]
    assert paper["nodes"] > 100 and paper["dropped"] == {"empty": 1} and "findings" in paper and isinstance(paper["counts"], dict)
    assert not [f for f in paper["findings"] if f["severity"] == "error"]
    assert by(events, "3")[0] == {"event": "audit", "id": "3", "papers": []}

