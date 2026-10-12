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



def test_a_pdf_that_prints_no_identifier_is_read_by_its_name(tmp_path):
    """A paper printing neither a DOI nor a PMCID is filed under its content hash, and a hash
    meets nothing — it never finds the same paper's JATS, so the witness pair is lost in
    silence. 15 of the campaign corpus's 334 PDFs were like that, and every one was *named*
    after its PMCID. The name is the file's own property, not a publisher's, so reading it
    transfers; it is tried last, because what a paper prints about itself beats what someone
    called the file."""
    from litrag_parser.worker import sniff_ids

    blank = tmp_path / "PMC13267673.pdf"
    blank.write_bytes(b"not a pdf at all")  # pdfium reads nothing; the name is all there is
    assert sniff_ids(blank) == (None, "PMC13267673")

    doi_named = tmp_path / "10.1113_jp289183.pdf"
    doi_named.write_bytes(b"not a pdf at all")
    assert sniff_ids(doi_named) == ("10.1113/jp289183", None)

    anonymous = tmp_path / "paper-final-v2.pdf"
    anonymous.write_bytes(b"not a pdf at all")
    assert sniff_ids(anonymous) == (None, None)  # nothing to read: the hash key is right

    assert sniff_ids(tmp_path / "PMC1.xml") == (None, None)  # JATS is read by jats_ids, not here


def test_a_readings_floats_note_replaces_the_last_readings(tmp_path):
    from types import SimpleNamespace

    from litrag_parser.store import open_store
    from litrag_parser.worker import Worker

    conn = open_store(tmp_path / "store.sqlite")
    note = {"kind": "xml-floats", "node_id": "k", "page": None, "message": "the XML holds 2 figures and 0 tables; this reading has 0 and 0"}
    for _ in range(2):
        Worker._note_events(conn, "k", SimpleNamespace(notes=[note]))
    assert [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = 'k' AND stage = 'xml-floats'")] == ["k: the XML holds 2 figures and 0 tables; this reading has 0 and 0"]
    Worker._note_events(conn, "k", SimpleNamespace(notes=[]))  # a reading that has every float: the last one's note goes
    assert conn.execute("SELECT count(*) FROM events WHERE paper = 'k'").fetchone()[0] == 0


def test_a_reading_with_fewer_floats_than_its_xml_says_so(tmp_path, monkeypatch):
    """The layout stood in by the micromachines paper's saved Docling document, its XML given one
    figure more than that document read: the 'saved' stage says so and one `xml-floats` row
    holds it however often the paper is read or rebuilt, the audit's reading says it too, a
    rebuild against the file it has all of clears it, and the parser string names Docling's
    version though no converter was built here."""
    from importlib.metadata import PackageNotFoundError, version

    from litrag_parser.harness import read_paper
    from litrag_parser.library import create_library, parsed_papers
    from litrag_parser.store import file_paper, open_store
    from litrag_parser.worker import Worker

    monkeypatch.setenv("LITRAG_BOUNDARY", "off")
    monkeypatch.delenv("LITRAG_JUDGE", raising=False)
    lib = create_library(tmp_path, "Micromachines")
    conn = open_store(lib.store_path)
    key = file_paper(conn, title="x", file="paper.xml", sha256="aa", fmt="jats", doi="10.3390/mi15070851", pmid=None, pmcid=None, now="t").key
    conn.commit()
    xml = (FIXTURES / "PMC11278924.xml").read_bytes()
    (lib.papers_dir / "paper.xml").write_bytes(xml.replace(b"</article>", b'<floats-group><fig id="f7"><label>Figure 7</label><caption><p>Cited nowhere.</p></caption></fig></floats-group></article>'))
    doc = (FIXTURES / "PMC11278924.jats.docling.json").read_text("utf-8")

    def layout(self, lib, key, path, raw_path, *rest):
        raw_path.write_text(doc, "utf-8")
        return json.loads(doc)

    monkeypatch.setattr(Worker, "layout", layout)
    worker = Worker(tmp_path)
    for _ in range(2):
        worker.parse_one(lib, key, lib.papers_dir / "paper.xml", "r")
    note = "the XML holds 7 figures and 1 table; this reading has 6 and 1"
    notes = lambda: [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'xml-floats'", (key,))]  # noqa: E731
    assert notes() == [f"{key}: {note}"]
    saved = [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'saved' ORDER BY id", (key,))]
    assert len(saved) == 2 and saved[-1].endswith(f"s, {note}")
    parser = conn.execute("SELECT parser FROM papers WHERE key = ?", (key,)).fetchone()[0]
    try:
        installed = version("docling")
    except PackageNotFoundError:
        installed = "?"
    assert parser.startswith(f"docling {installed} · ")
    probe = "import sys; from litrag_parser.worker import docling_version; docling_version(); print('docling' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout.strip() == "False"  # read from its metadata, never imported for it
    worker.do_rebuild({"id": "a", "op": "rebuild", "lib": lib.id})
    assert notes() == [f"{key}: {note}"]  # a rebuild against the same file says it the same, once
    assert conn.execute("SELECT count(*) FROM events WHERE paper = ? AND stage = 'rebuild-refused'", (key,)).fetchone()[0] == 0
    tree, _, _ = read_paper(lib.dir, parsed_papers(lib.dir, [key])[0])
    assert [n["message"] for n in tree.notes if n["kind"] == "xml-floats"] == [note]  # the library audit's reading
    (lib.papers_dir / "paper.xml").write_bytes(xml)
    worker.do_rebuild({"id": "b", "op": "rebuild", "lib": lib.id})
    assert conn.execute("SELECT count(*) FROM events WHERE paper = ? AND stage = 'xml-floats'", (key,)).fetchone()[0] == 0
