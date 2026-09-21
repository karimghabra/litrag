"""A paper filed but never read, and a paper the worker died on.

`status` says what the reader last intended; the nodes say what it produced. They came apart
when the worker's native crash left seventeen PDFs with a `parsed` row and nothing under it
(BACKLOG.md), and nothing noticed for a day, because the skip keyed on the row: every later
ingest read `parsed` and passed the file over, and `rebuild` never visits a paper whose raw
document is missing. These tests make each of those states, and check something now says so.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from litrag_parser.harness import unread_papers
from litrag_parser.library import create_library
from litrag_parser.store import node_count, open_store
from litrag_parser.worker import _has_a_tree

FIXTURES = Path(__file__).parent / "fixtures"


def _library(root: Path, name: str = "Test"):
    lib = create_library(root, name, None)
    lib.ensure_dirs()
    return lib


def _file_a_paper(lib, key: str, *, status: str, nodes: int, raw: bool) -> None:
    """A row in whatever state the crash would have left it, with or without its tree."""
    conn = open_store(lib.store_path)
    with conn:
        conn.execute(
            "INSERT INTO papers(key, title, file, format, status, added_at) VALUES (?,?,?,?,?,?)",
            (key, key, f"{key}.pdf", "pdf", status, "2026-09-21T00:00:00Z"),
        )
        for i in range(nodes):
            conn.execute(
                """INSERT INTO nodes(node_id, paper, ordinal, depth, type, label, role, ancestry, text)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (f"{key}#p{i}", key, i, 1, "paragraph", "text", "methods", "[]", "some prose"),
            )
    conn.close()
    if raw:
        (lib.parsed_dir / f"{key}.docling.json").write_text("{}", encoding="utf-8")


def test_a_paper_with_no_nodes_is_not_read_whatever_the_row_says(tmp_path):
    lib = _library(tmp_path)
    _file_a_paper(lib, "whole", status="parsed", nodes=3, raw=True)
    _file_a_paper(lib, "no_nodes", status="parsed", nodes=0, raw=True)
    _file_a_paper(lib, "no_raw", status="parsed", nodes=3, raw=False)
    conn = open_store(lib.store_path)
    try:
        assert node_count(conn, "whole") == 3 and node_count(conn, "no_nodes") == 0
        assert _has_a_tree(conn, lib, "whole")
        assert not _has_a_tree(conn, lib, "no_nodes")  # the row claims a tree the store has not
        assert not _has_a_tree(conn, lib, "no_raw")  # nothing a rebuild could derive it from again
    finally:
        conn.close()


def test_the_harness_names_the_papers_that_are_in_no_line_of_its_report(tmp_path):
    lib = _library(tmp_path)
    _file_a_paper(lib, "whole", status="parsed", nodes=3, raw=True)
    _file_a_paper(lib, "no_nodes", status="parsed", nodes=0, raw=True)
    _file_a_paper(lib, "no_raw", status="parsed", nodes=3, raw=False)
    _file_a_paper(lib, "stuck", status="parsing", nodes=0, raw=False)
    _file_a_paper(lib, "broke", status="failed", nodes=0, raw=False)
    found = unread_papers(lib.dir)
    assert found["no_nodes"] == ["no_nodes"]
    assert found["no_raw"] == ["no_raw"]
    assert found["unfinished"] == ["stuck [parsing]"]
    assert found["failed"] == ["broke"]
    assert "whole" not in json.dumps(found)  # the one good paper is not named
    # every paper lands in exactly one bucket: a row with neither nodes nor a raw document
    # counted twice would make the gate's total larger than the number of papers it is about
    named = [k for keys in found.values() for k in keys]
    assert len(named) == len(set(named)) == 4


def test_the_gate_fires_on_a_broken_claim_and_not_on_an_honest_failure(tmp_path):
    """A PDF Docling genuinely cannot read stays `failed` — a terminal state with a reason. A
    gate that goes red on it forever teaches people to stop passing `--gate`."""
    from litrag_parser.harness import BROKEN

    lib = _library(tmp_path)
    _file_a_paper(lib, "whole", status="parsed", nodes=3, raw=True)
    _file_a_paper(lib, "broke", status="failed", nodes=0, raw=False)
    found = unread_papers(lib.dir)
    assert sum(len(found[k]) for k in BROKEN) == 0  # nothing to gate on
    assert found["failed"] == ["broke"]  # but it is still reported

    _file_a_paper(lib, "no_nodes", status="parsed", nodes=0, raw=True)
    found = unread_papers(lib.dir)
    assert sum(len(found[k]) for k in BROKEN) == 1


def test_a_healthy_library_reports_nothing(tmp_path):
    lib = _library(tmp_path)
    _file_a_paper(lib, "whole", status="parsed", nodes=3, raw=True)
    empty = {"no_nodes": [], "no_raw": [], "unfinished": [], "failed": []}
    assert unread_papers(lib.dir) == empty
    assert unread_papers(tmp_path / "not-a-library") == empty


def test_a_paper_left_parsing_is_given_a_terminal_state_when_the_worker_next_opens_it(tmp_path):
    """`parsing` is set before the layout stage and cleared only by `save_tree`, so a worker
    that dies — or is quit — mid-paper leaves it set for good: the card's progress bar never
    stops and the paper is reported as nothing at all."""
    lib = _library(tmp_path)
    _file_a_paper(lib, "stuck", status="parsing", nodes=0, raw=False)
    lines = "".join(json.dumps(r) + "\n" for r in [
        {"id": "1", "op": "papers", "lib": lib.id},
        {"id": "q", "op": "quit"},
    ])
    proc = subprocess.run(
        [sys.executable, "-m", "litrag_parser.worker", f"--root={tmp_path}"],
        input=lines, capture_output=True, text=True, encoding="utf-8", timeout=120,
        cwd=Path(__file__).parent.parent,
    )
    assert proc.returncode == 0, proc.stderr
    events = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    recovered = [e for e in events if e.get("event") == "recovered"]
    assert recovered and recovered[0]["papers"] == ["stuck"]

    conn = sqlite3.connect(str(lib.store_path))
    try:
        status, error = conn.execute("SELECT status, error FROM papers WHERE key = 'stuck'").fetchone()
        stages = [r[0] for r in conn.execute("SELECT stage FROM events WHERE paper = 'stuck'")]
    finally:
        conn.close()
    assert status == "failed" and "stopped while reading" in (error or "")
    assert "interrupted" in stages  # and the history says why, not just that it failed
