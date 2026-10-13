"""projects.py: a project's summary, its description, and one library made of several — each
paper filed once, its raw document, the PDF kept beside it for its figures and its stored answers
carried, the sources left as they were."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from litrag_parser import acquire, figures, projects
from litrag_parser.library import create_library, open_library, parsed_papers, safe_key
from litrag_parser.store import file_paper, open_store, save_judgment, save_tree, set_record, set_type, sha256_of
from litrag_parser.tree import build_tree

FIXTURES = Path(__file__).parent / "fixtures"
DOC = FIXTURES / "PMC11278924.docling.json"
SHARED = "10.3390/mi15070851"
PAGE = FIXTURES / "charts" / "page_paper.pdf"
PRINTED = "Fold change of tenogenic markers on each substrate"  # a line page_paper.pdf prints, which prints no DOI: its first pages name a paper so titled
KEY = f"doi:{SHARED}"
KEPT = f"{safe_key(KEY)}.figures.pdf"


def _add(lib, conn, *, doi, pmid=None, pmcid=None, source: Path, fmt: str, raw: bool = True, tree: bool = True):
    """A paper as the worker leaves one: its file in papers/, its raw document in parsed/, its rows."""
    sha = sha256_of(source)
    filed = file_paper(conn, title=source.stem, file="", sha256=sha, fmt=fmt, doi=doi, pmid=pmid, pmcid=pmcid, now="2026-09-01T00:00:00Z")
    dest = lib.papers_dir / f"{safe_key(filed.key)}{source.suffix}"
    shutil.copy2(source, dest)
    conn.execute("UPDATE papers SET file = ?, format = ?, status = 'parsed' WHERE key = ?", (dest.name, fmt, filed.key))
    conn.commit()
    if raw:
        shutil.copy2(DOC, lib.parsed_dir / f"{safe_key(filed.key)}.docling.json")
    if tree:
        save_tree(conn, filed.key, build_tree(json.loads(DOC.read_text("utf-8")), filed.key), parser="test", parsed_at="t", seconds=0.0)
    else:
        conn.execute("UPDATE papers SET status = 'queued' WHERE key = ?", (filed.key,))
        conn.commit()
    return filed.key


def _digest(lib) -> dict[str, str]:
    """Every file of a library by its bytes — but SQLite's own `-wal`/`-shm` housekeeping, which a
    read-only reader of a WAL store leaves behind (empty of data) and cannot remove."""
    return {str(p.relative_to(lib.dir)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(lib.dir.rglob("*"))
            if p.is_file() and not p.name.endswith(("-wal", "-shm"))}


@pytest.fixture
def two(tmp_path):
    root = tmp_path / "root"
    a = create_library(root, "Collagen A")
    b = create_library(root, "Collagen B")
    ca = open_store(a.store_path)
    ka = _add(a, ca, doi=SHARED, pmid="39064362", pmcid="PMC11278924", source=FIXTURES / "PMC11278924.xml", fmt="jats")
    set_record(ca, ka, pub_types=["research-article", "Journal Article"], authors=[{"name": "Shengmao Lin", "affiliations": [], "corresponding": False}], journal="Micromachines", year="2024")
    set_type(ca, ka, "research", "record", "record says research-article")
    with ca:
        ca.execute("INSERT INTO outlines(paper, signature, model, outline, prompt_tokens, answer_tokens, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
                   (ka, "sig1", "qwen3:14b", '[{"title": "Introduction"}]', 10, 5, 1.5, "t"))
    save_judgment(ca, ka, "pair1", True, "qwen3:14b", "t")
    acquire.record_search(a, ca, "aligned collagen", [
        {"doi": SHARED, "pmid": "39064362", "pmcid": "PMC11278924", "title": "Aligned collagen", "has_xml": True, "is_open_access": True},
        {"doi": "10.9/only-a", "title": "only in A"},
    ])
    ca.close()

    cb = open_store(b.store_path)
    # the same paper, held here as a PDF under the same DOI written in another case
    pdf = b.dir / "the-same.pdf"
    pdf.write_bytes(b"%PDF-1.4 the same paper as a PDF")
    kb_same = _add(b, cb, doi=SHARED.upper(), source=pdf, fmt="pdf")
    other = b.dir / "other.pdf"
    other.write_bytes(b"%PDF-1.4 another paper")
    kb_other = _add(b, cb, doi="10.1/other", source=other, fmt="pdf")
    save_judgment(cb, kb_same, "pair-b", False, "qwen3:14b", "t")
    unread = b.dir / "unread.pdf"
    unread.write_bytes(b"%PDF-1.4 filed, never read")
    kb_unread = _add(b, cb, doi=None, pmid="555", source=unread, fmt="pdf", raw=False, tree=False)
    acquire.record_search(b, cb, "collagen crosslinking", [
        {"doi": SHARED, "title": "Aligned collagen again"},
        {"doi": "10.9/only-b", "title": "only in B"},
    ])
    cb.execute("UPDATE candidates SET status = 'dismissed' WHERE doi = '10.9/only-b'")
    cb.commit()
    cb.close()
    for p in (pdf, other, unread):
        p.unlink()
    return {"root": root, "a": a, "b": b, "ka": ka, "kb_same": kb_same, "kb_other": kb_other, "kb_unread": kb_unread}


def test_merge_files_a_shared_doi_once(two):
    root, a, b = two["root"], two["a"], two["b"]
    before = (_digest(a), _digest(b))
    out = projects.merge(root, ["collagen-a", "collagen-b"], "Collagen")
    assert (_digest(a), _digest(b)) == before  # the sources are only read
    assert out["target"]["id"] == "collagen" and out["sources"] == ["collagen-a", "collagen-b"]
    assert out["filed"] == 3 and out["duplicates"] == 1 and out["skipped"] == []
    assert out["keys_to_rebuild"] == [two["ka"], two["kb_other"]]
    assert out["keys_to_parse"] == [two["kb_unread"]]
    assert out["candidates_merged"] == 3  # the shared DOI's candidate once, one from each side
    assert out["queries_merged"] == 2

    t = open_library(root, "collagen")
    conn = open_store(t.store_path)
    papers = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM papers")}
    assert set(papers) == {two["ka"], two["kb_other"], two["kb_unread"]}
    first = papers[two["ka"]]
    assert first["format"] == "jats" and first["status"] == "parsed" and first["file"] == f"{safe_key(two['ka'])}.xml"  # the first one read wins
    assert first["doi"] == SHARED  # spelled as A filed it, first: B's, in capitals, met it in any case
    assert (t.papers_dir / first["file"]).read_bytes() == (FIXTURES / "PMC11278924.xml").read_bytes()
    assert first["journal"] == "Micromachines" and first["year"] == "2024" and first["type"] == "research"
    assert first["pub_types"] == "research-article; Journal Article" and json.loads(first["authors"])[0]["name"] == "Shengmao Lin"
    assert papers[two["kb_unread"]]["status"] == "queued" and papers[two["kb_unread"]]["file"]
    assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0  # rows are the worker's rebuild's to derive
    assert [tuple(r) for r in conn.execute("SELECT paper, signature, model FROM outlines")] == [(two["ka"], "sig1", "qwen3:14b")]
    assert [tuple(r) for r in conn.execute("SELECT paper, pair, same FROM judgments")] == [(two["ka"], "pair1", 1)]  # a duplicate's answers stay with it
    # what the worker's rebuild will read: both raw documents, under the target's keys
    assert sorted(p["key"] for p in parsed_papers(t.dir)) == sorted([two["ka"], two["kb_other"]])
    cands = {c["doi"]: c for c in acquire.candidates(conn)}
    assert set(cands) == {SHARED.lower(), "10.9/only-a", "10.9/only-b"}
    assert cands[SHARED.lower()]["status"] == "ingested" and cands[SHARED.lower()]["paper_key"] == two["ka"]
    assert cands["10.9/only-b"]["status"] == "dismissed"
    m = json.loads(t.manifest_path.read_text("utf-8"))
    assert [q["query"] for q in m["queries"]] == ["aligned collagen", "collagen crosslinking"] and m["mergedFrom"] == ["collagen-a", "collagen-b"]

    # merging again files nothing; what still wants a rebuild is still named
    again = projects.merge(root, ["collagen-a", "collagen-b"], "Collagen", into="collagen")
    assert again["filed"] == 0 and again["duplicates"] == 4 and again["candidates_merged"] == 0 and again["queries_merged"] == 0
    assert again["keys_to_rebuild"] == out["keys_to_rebuild"]

    # once the rows are there (as the worker's rebuild leaves them), a third merge names nothing
    for k in out["keys_to_rebuild"]:
        save_tree(conn, k, build_tree(json.loads(DOC.read_text("utf-8")), k), parser="rebuild", parsed_at="t", seconds=0.0)
    counts = conn.execute("SELECT (SELECT COUNT(*) FROM papers), (SELECT COUNT(*) FROM candidates), (SELECT COUNT(*) FROM events), (SELECT COUNT(*) FROM outlines)").fetchone()
    third = projects.merge(root, ["collagen-a", "collagen-b"], "Collagen", into="collagen")
    assert third["filed"] == 0 and third["keys_to_rebuild"] == []
    assert conn.execute("SELECT (SELECT COUNT(*) FROM papers), (SELECT COUNT(*) FROM candidates), (SELECT COUNT(*) FROM events), (SELECT COUNT(*) FROM outlines)").fetchone() == counts
    conn.close()


def test_merge_files_a_doi_in_another_case_once_whichever_source_comes_first(two):
    out = projects.merge(two["root"], ["collagen-b", "collagen-a"], "Collagen")
    assert out["filed"] == 3 and out["duplicates"] == 1
    conn = open_store(open_library(two["root"], "collagen").store_path)
    held = {r["key"]: r["doi"] for r in conn.execute("SELECT key, doi FROM papers")}
    assert held == {two["kb_same"]: SHARED.upper(), two["kb_other"]: "10.1/other", two["kb_unread"]: None}  # B's spelling, filed first, is kept
    again = projects.merge(two["root"], ["collagen-a", "collagen-b"], "Collagen", into="collagen")
    assert again["filed"] == 0 and conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 3
    conn.close()


def test_merge_refuses_what_it_cannot_do(two):
    root = two["root"]
    with pytest.raises(ValueError, match="no such library"):
        projects.merge(root, ["nope"], "X")
    with pytest.raises(ValueError, match="already exists"):
        projects.merge(root, ["collagen-a"], "Collagen B")
    with pytest.raises(ValueError, match="into itself"):
        projects.merge(root, ["collagen-a", "collagen-b"], "", into="collagen-b")


def test_merge_carries_the_labels_a_person_made(two):
    """A person's finding→method labels (and the local model's) are not derived from the paper: they travel with it, a
    duplicate's as well, under the key the target holds it by; merging again adds nothing."""
    root, ka, kb = two["root"], two["ka"], two["kb_same"]
    rows = {"a": (ka, f"{ka}#section-5#paragraph-1", "The aligned threads were stronger.", f"{ka}#section-4#section-6", "2.6. Mechanical Assessment", "yes"),
            "b": (kb, f"{kb}#section-5#paragraph-2", "The random threads were weaker.", "", "", "none")}
    for side, row in rows.items():
        conn = open_store(two[side].store_path)
        with conn:
            conn.execute("INSERT INTO link_labels(paper, finding, finding_text, method, method_heading, verdict, by, at) VALUES (?,?,?,?,?,?,'Karim','t')", row)
            if side == "a":  # the local model's labels travel too, in their own table
                conn.execute("INSERT INTO model_labels(paper, finding, finding_text, method, method_heading, verdict, by, at) VALUES (?,?,?,?,?,?,'qwen3:14b','t')", row)
        conn.close()
    projects.merge(root, ["collagen-a", "collagen-b"], "Collagen")
    projects.merge(root, ["collagen-a", "collagen-b"], "Collagen", into="collagen")
    conn = open_store(open_library(root, "collagen").store_path)
    got = sorted(tuple(r) for r in conn.execute("SELECT paper, finding, method, verdict, by FROM link_labels"))
    model = [tuple(r) for r in conn.execute("SELECT paper, finding, method, verdict, by FROM model_labels")]
    conn.close()
    assert model == [(ka, f"{ka}#section-5#paragraph-1", f"{ka}#section-4#section-6", "yes", "qwen3:14b")]
    assert kb != ka  # the same DOI in another case: filed once, under the first key
    assert got == sorted([(ka, f"{ka}#section-5#paragraph-1", f"{ka}#section-4#section-6", "yes", "Karim"), (ka, f"{ka}#section-5#paragraph-2", "", "none", "Karim")])


def test_summary_and_describe(two):
    a = two["a"]
    s = projects.summary(a)
    assert s["id"] == "collagen-a" and s["name"] == "Collagen A" and s["description"] is None
    assert [q["query"] for q in s["queries"]] == ["aligned collagen"]
    c = s["counts"]
    assert c["papers"] == 1 and c["parsed"] == 1 and c["failed"] == 0 and c["xml"] == 1 and c["pdf"] == 0
    assert c["by_type"] == {"research": 1} and c["nodes"] > 0 and c["vectors"] == 0
    assert c["candidates"] == {"found": 1, "ingested": 1}
    b = projects.summary(two["b"])["counts"]
    assert b["pdf"] == 3 and b["parsed"] == 2 and b["by_type"] == {"unknown": 3} and b["candidates"] == {"dismissed": 1, "ingested": 1}

    d = projects.describe(a, name="Collagen, aligned", description="  How crosslinking changes aligned collagen's modulus.  ")
    assert d["name"] == "Collagen, aligned" and d["description"] == "How crosslinking changes aligned collagen's modulus."
    m = json.loads(a.manifest_path.read_text("utf-8"))
    assert m["id"] == "collagen-a" and m["description"].startswith("How") and list(m) == sorted(m)
    assert projects.describe(a, description="x")["name"] == "Collagen, aligned"  # a name not given is kept
    with pytest.raises(ValueError):
        projects.describe(a, name="  ")


def test_summary_counts_vectors_and_tolerates_no_store(tmp_path):
    lib = create_library(tmp_path, "Empty")
    s = projects.summary(lib)
    assert s["counts"]["papers"] == 0 and s["counts"]["candidates"] == {} and not lib.store_path.exists()  # read-only: nothing made
    conn = open_store(lib.store_path)
    conn.execute("CREATE TABLE vectors (chunk INTEGER, model TEXT, v BLOB)")
    conn.executemany("INSERT INTO vectors VALUES (?,?,?)", [(1, "m", b""), (2, "m", b"")])
    conn.commit()
    conn.close()
    assert projects.summary(lib)["counts"]["vectors"] == 2


# -- the PDF kept beside an XML paper for its figures ------------------------------------------------------------


def _read_into_rows(conn, key, title=PRINTED):
    """The paper's rows, as the worker's rebuild leaves them, under `title`."""
    save_tree(conn, key, build_tree(json.loads(DOC.read_text("utf-8")), key), parser="rebuild", parsed_at="t", seconds=0.0)
    conn.execute("UPDATE papers SET title = ? WHERE key = ?", (title, key))
    conn.commit()


def _beside(lib, conn, key):
    """page_paper.pdf kept beside the paper's XML for its figures — under the name a paper's own PDF
    has, as an XML read over it keeps it — and the read of it: one plot read, one under no caption."""
    name = f"{safe_key(key)}.pdf"
    shutil.copy2(PAGE, lib.papers_dir / name)
    figures.ensure_schema(conn)
    with conn:
        conn.execute("UPDATE papers SET figures_file = ? WHERE key = ?", (name, key))
        conn.execute("INSERT INTO charts(paper, figure, figure_label, plot, page, bbox, status, source, reader, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (key, figures.figure_numbers(conn, key)["1"], "1", 1, 2, "[0, 0, 100, 100]", "read", "vector", figures.READER, "t"))
        conn.execute("INSERT INTO figure_reads(paper, reader, figures, plots, read, values_, seconds, at, file, unmatched) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (key, figures.READER, 1, 2, 1, 0, 1.0, "the source's read", name, 1))
    return name


@pytest.fixture
def threads(tmp_path):
    """Two libraries holding the same paper read from XML, titled with a line page_paper.pdf prints:
    `threads` with that PDF kept beside it for its figures, and the read of it; `bare` without."""
    root = tmp_path / "root"
    s = create_library(root, "Threads")
    conn = open_store(s.store_path)
    assert _add(s, conn, doi=SHARED, pmid="39064362", pmcid="PMC11278924", source=FIXTURES / "PMC11278924.xml", fmt="jats") == KEY
    _read_into_rows(conn, KEY)
    name = _beside(s, conn, KEY)
    conn.close()
    bare = create_library(root, "Bare")
    conn = open_store(bare.store_path)
    _add(bare, conn, doi=SHARED, pmid="39064362", pmcid="PMC11278924", source=FIXTURES / "PMC11278924.xml", fmt="jats")
    _read_into_rows(conn, KEY)
    conn.close()
    return {"root": root, "threads": s, "bare": bare, "name": name}


def _held(root, lib="merged"):
    t = open_library(root, lib)
    return t, open_store(t.store_path)


def _stages(conn, key=KEY):
    return [(r[0], r[1]) for r in conn.execute("SELECT stage, detail FROM events WHERE paper = ? ORDER BY id", (key,))]


def test_a_paper_new_to_the_target_brings_the_pdf_kept_beside_it_and_its_read(threads):
    root, s = threads["root"], threads["threads"]
    before = _digest(s)
    out = projects.merge(root, ["threads"], "Merged")
    assert _digest(s) == before  # the source is only read
    assert out["filed"] == 1 and (out["figures_kept"], out["keys_to_read_figures"], out["figures_not_kept"]) == ([], [], [])  # a copy of what the source admitted, not an arrival
    t, conn = _held(root)
    assert conn.execute("SELECT figures_file FROM papers WHERE key = ?", (KEY,)).fetchone()[0] == KEPT
    assert (t.papers_dir / KEPT).read_bytes() == PAGE.read_bytes()
    read = conn.execute("SELECT file, unmatched, plots, at FROM figure_reads WHERE paper = ?", (KEY,)).fetchone()
    assert tuple(read) == (KEPT, 1, 2, "the source's read")  # the file it read, by the name the target keeps it under
    assert conn.execute("SELECT COUNT(*) FROM charts WHERE paper = ?", (KEY,)).fetchone()[0] == 1
    assert [stage for stage, _ in _stages(conn)] == ["merged"]  # no 'filed' row: nothing arrived
    _read_into_rows(conn, KEY)  # as the worker's rebuild leaves it
    assert figures.figures_wanted(conn, t.papers_dir) == [] and figures.is_read(conn, KEY)
    conn.close()


def test_a_paper_new_to_the_target_brings_pmc_s_answer_for_its_figures(threads):
    root, bare = threads["root"], threads["bare"]
    note = "PMC Cloud: not in its open-access datasets; bulk area: HTTP 404"
    conn = open_store(bare.store_path)
    conn.execute("UPDATE papers SET figures_note = ? WHERE key = ?", (note, KEY))
    conn.commit()
    conn.close()
    assert projects.merge(root, ["bare"], "Merged")["filed"] == 1
    t, conn = _held(root)
    assert tuple(conn.execute("SELECT figures_file, figures_note FROM papers WHERE key = ?", (KEY,)).fetchone()) == (None, note)
    conn.close()


def test_a_pdf_the_source_names_but_no_longer_has_is_not_carried(threads):
    root, s = threads["root"], threads["threads"]
    (s.papers_dir / threads["name"]).unlink()
    out = projects.merge(root, ["threads"], "Merged")  # a new paper: nothing to copy, and nothing raised
    assert out["filed"] == 1 and (out["figures_kept"], out["figures_not_kept"]) == ([], [])
    t, conn = _held(root)
    assert conn.execute("SELECT figures_file FROM papers WHERE key = ?", (KEY,)).fetchone()[0] is None
    assert list(t.papers_dir.glob("*.pdf")) == []
    # its read names the file it read, which the target was not given: it is no read of a file kept there
    assert conn.execute("SELECT file FROM figure_reads WHERE paper = ?", (KEY,)).fetchone()[0] == threads["name"]
    _read_into_rows(conn, KEY)
    assert not figures.is_read(conn, KEY) and [w["key"] for w in figures.figures_wanted(conn, t.papers_dir)] == [KEY]
    conn.close()
    # the paper held already: nothing to keep, nothing refused
    projects.merge(root, ["bare"], "Bare too")
    again = projects.merge(root, ["threads"], "", into="bare-too")
    assert again["duplicates"] == 1 and (again["figures_kept"], again["keys_to_read_figures"], again["figures_not_kept"]) == ([], [], [])


def test_a_paper_the_target_holds_without_a_pdf_takes_the_source_s_through_the_gate(threads):
    root = threads["root"]
    projects.merge(root, ["bare"], "Merged")
    t, conn = _held(root)
    _read_into_rows(conn, KEY)  # as the worker's rebuild leaves it
    out = projects.merge(root, ["threads"], "", into="merged")
    assert out["duplicates"] == 1 and (out["figures_kept"], out["keys_to_read_figures"], out["figures_not_kept"]) == ([KEY], [KEY], [])
    assert conn.execute("SELECT figures_file FROM papers WHERE key = ?", (KEY,)).fetchone()[0] == KEPT and (t.papers_dir / KEPT).read_bytes() == PAGE.read_bytes()
    filed = [d for stage, d in _stages(conn) if stage == "filed"]
    assert filed == [f"a PDF of the XML, kept for its figures: {threads['name']} (its pages print the captions of 2 of its 6 figures)"]
    assert conn.execute("SELECT COUNT(*) FROM charts WHERE paper = ?", (KEY,)).fetchone()[0] == 0  # a duplicate's charts stay in its source: the worker reads the PDF
    # merging again, and a third time, changes nothing: the PDF is kept there now
    events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    for _ in range(2):
        again = projects.merge(root, ["bare", "threads"], "", into="merged")
        assert (again["figures_kept"], again["keys_to_read_figures"], again["figures_not_kept"]) == ([], [], [])
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events
    assert sorted(p.name for p in t.papers_dir.glob("*.figures*")) == [KEPT]
    conn.close()


def test_a_pdf_kept_in_the_merge_that_brings_its_paper_is_counted_against_no_reading(threads):
    """The paper new from one source, its PDF from another in the same merge: kept before the
    rebuild has read the paper into rows, so its figures are not counted, and said so."""
    out = projects.merge(threads["root"], ["bare", "threads"], "Merged")
    assert (out["filed"], out["duplicates"], out["keys_to_rebuild"], out["keys_to_read_figures"]) == (1, 1, [KEY], [KEY])
    _, conn = _held(threads["root"])
    assert [d for stage, d in _stages(conn) if stage == "filed"] == [f"a PDF of the XML, kept for its figures: {threads['name']} (its XML not yet read into rows)"]
    conn.close()


def test_charts_an_earlier_merge_copied_without_their_pdf_become_its_read_once_it_comes(threads):
    root = threads["root"]
    projects.merge(root, ["threads"], "Merged")
    t, conn = _held(root)
    # as a merge made before one carried the PDF left the paper: its charts, and a read naming no file
    (t.papers_dir / KEPT).unlink()
    with conn:
        conn.execute("UPDATE papers SET figures_file = NULL WHERE key = ?", (KEY,))
        conn.execute("UPDATE figure_reads SET file = NULL WHERE paper = ?", (KEY,))
    _read_into_rows(conn, KEY)
    out = projects.merge(root, ["threads"], "", into="merged")
    assert (out["figures_kept"], out["keys_to_read_figures"], out["figures_not_kept"]) == ([KEY], [], [])  # nothing read again
    assert tuple(conn.execute("SELECT file, at FROM figure_reads WHERE paper = ?", (KEY,)).fetchone()) == (KEPT, "the source's read")
    assert conn.execute("SELECT COUNT(*) FROM charts WHERE paper = ?", (KEY,)).fetchone()[0] == 1 and figures.is_read(conn, KEY)
    assert len([d for stage, d in _stages(conn) if stage == "filed"]) == 1
    conn.close()


def test_a_pdf_whose_pages_do_not_name_the_target_s_paper_is_not_kept_and_written_nowhere(threads):
    root = threads["root"]
    projects.merge(root, ["bare"], "Merged")
    t, conn = _held(root)
    _read_into_rows(conn, KEY, title="Aligned collagen threads, read from XML and titled otherwise")
    events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    refused = [{"library": "threads", "paper": KEY, "reason": "its first pages name neither its DOI nor its title"}]
    for _ in range(2):  # a merge run twice says it twice, and writes nothing either time
        out = projects.merge(root, ["threads"], "", into="merged")
        assert (out["figures_kept"], out["keys_to_read_figures"], out["figures_not_kept"]) == ([], [], refused)
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events
    assert conn.execute("SELECT figures_file FROM papers WHERE key = ?", (KEY,)).fetchone()[0] is None and list(t.papers_dir.glob("*.pdf")) == []
    conn.close()


def _merge_op(root, monkeypatch, capsys, **req):
    """The worker's merge, its rebuild, reparse and figures ops stood in for: its events, and the
    requests each was given (`order`: the ops, as they were asked)."""
    from litrag_parser.worker import Worker

    asked: dict[str, list] = {"rebuild": [], "reparse": [], "figures": [], "order": []}
    for op in ("rebuild", "reparse", "figures"):
        monkeypatch.setattr(Worker, f"do_{op}", lambda self, r, op=op: (asked[op].append(r), asked["order"].append(op)))
    capsys.readouterr()
    Worker(root).do_merge({"id": "m", "op": "merge", **req})
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()], asked


def test_a_merge_reads_the_figures_of_the_papers_it_kept_a_pdf_beside(threads, monkeypatch, capsys):
    root = threads["root"]
    projects.merge(root, ["bare"], "Merged")
    _, conn = _held(root)
    _read_into_rows(conn, KEY)
    conn.close()
    events, asked = _merge_op(root, monkeypatch, capsys, sources=["threads"], into="merged")
    assert asked["figures"] == [{"id": "m-figures", "op": "figures", "lib": "merged", "keys": [KEY], "force": True}]
    done = events[-1]
    assert (done["event"], done["op"], done["figures"], done["figures_not_kept"]) == ("done", "merge", [KEY], [])
    # a paper read again reads its figures as it is read: not asked twice; one derived again is read after it is
    held = {"target": {"id": "merged"}, "sources": ["threads"], "filed": 0, "duplicates": 3, "skipped": [], "keys_to_rebuild": ["doi:10.1/derived"], "keys_to_parse": ["doi:10.1/again"],
            "figures_kept": ["doi:10.1/again", "doi:10.1/derived", KEY], "keys_to_read_figures": ["doi:10.1/again", "doi:10.1/derived", KEY], "figures_not_kept": [], "candidates_merged": 0, "queries_merged": 0}
    monkeypatch.setattr(projects, "merge", lambda *a, **kw: held)
    _, asked = _merge_op(root, monkeypatch, capsys, sources=["threads"], into="merged")
    assert [r["keys"] for r in asked["reparse"]] == [["doi:10.1/again"]] and [r["keys"] for r in asked["figures"]] == [["doi:10.1/derived", KEY]]
    assert asked["order"] == ["rebuild", "reparse", "figures"]
    # with the figures off, every one is kept and not read: the reading again reads none either
    monkeypatch.setenv("LITRAG_FIGURES", "off")
    events, asked = _merge_op(root, monkeypatch, capsys, sources=["threads"], into="merged")
    assert asked["order"] == ["rebuild", "reparse"]
    assert [e["message"] for e in events if e["event"] == "stage" and e["stage"] == "figures"] == ["3 kept for their figures; not read: LITRAG_FIGURES=off"]


def test_a_paper_new_from_one_source_with_its_pdf_from_another_is_derived_then_its_figures_read(threads, monkeypatch, capsys):
    """Its rebuild reads no figures, and the figures op reads them against its rows: derived first."""
    events, asked = _merge_op(threads["root"], monkeypatch, capsys, sources=["bare", "threads"], name="Merged")
    assert [r["keys"] for r in asked["rebuild"]] == [[KEY]] and [r["keys"] for r in asked["figures"]] == [[KEY]]
    assert asked["order"] == ["rebuild", "figures"] and events[-1]["figures"] == [KEY]


def test_a_merge_says_the_pdfs_it_kept_and_those_it_did_not_whether_or_not_it_reads_them(threads, monkeypatch, capsys):
    root = threads["root"]
    projects.merge(root, ["bare"], "Merged")
    _, conn = _held(root)
    _read_into_rows(conn, KEY, title="Aligned collagen threads, read from XML and titled otherwise")
    conn.close()
    events, asked = _merge_op(root, monkeypatch, capsys, sources=["threads"], into="merged")
    assert asked["figures"] == [] and events[-1]["figures"] == []
    assert events[-1]["figures_not_kept"] == [{"library": "threads", "paper": KEY, "reason": "its first pages name neither its DOI nor its title"}]
    # kept for an earlier merge's copy of its charts: said, and nothing read
    projects.merge(root, ["threads"], "Copied")
    t, conn = _held(root, "copied")
    (t.papers_dir / KEPT).unlink()
    with conn:
        conn.execute("UPDATE papers SET figures_file = NULL WHERE key = ?", (KEY,))
        conn.execute("UPDATE figure_reads SET file = NULL WHERE paper = ?", (KEY,))
    _read_into_rows(conn, KEY)
    conn.close()
    events, asked = _merge_op(root, monkeypatch, capsys, sources=["threads"], into="copied")
    assert asked["figures"] == [] and (events[-1]["figures"], events[-1]["figures_not_kept"]) == ([KEY], [])


def test_a_merge_with_no_figures_to_read_asks_for_none(threads, monkeypatch, capsys):
    """Never the figures op with no keys, which reads every paper's."""
    root = threads["root"]
    events, asked = _merge_op(root, monkeypatch, capsys, sources=["threads"], name="Merged")  # a new paper: its read came with it
    assert asked["figures"] == [] and asked["rebuild"] and (events[-1]["figures"], events[-1]["figures_not_kept"]) == ([], [])
    # the paper held unread, its PDF kept beside it in the same merge: read with it, not after
    unread = create_library(root, "Unread")
    conn = open_store(unread.store_path)
    _add(unread, conn, doi=SHARED, source=FIXTURES / "PMC11278924.xml", fmt="jats", raw=False, tree=False)
    conn.execute("UPDATE papers SET title = ? WHERE key = ?", (PRINTED, KEY))
    conn.commit()
    conn.close()
    events, asked = _merge_op(root, monkeypatch, capsys, sources=["unread", "threads"], name="Both")
    assert [r["keys"] for r in asked["reparse"]] == [[KEY]] and asked["figures"] == []
    assert events[-1]["figures"] == [KEY]


def test_with_the_figures_off_a_merge_keeps_the_pdf_and_says_it_is_not_read(threads, monkeypatch, capsys):
    monkeypatch.setenv("LITRAG_FIGURES", "off")
    root = threads["root"]
    projects.merge(root, ["bare"], "Merged")
    t, conn = _held(root)
    _read_into_rows(conn, KEY)
    events, asked = _merge_op(root, monkeypatch, capsys, sources=["threads"], into="merged")
    assert asked["figures"] == []
    assert conn.execute("SELECT figures_file FROM papers WHERE key = ?", (KEY,)).fetchone()[0] == KEPT and (t.papers_dir / KEPT).exists()
    conn.close()
    said = [e for e in events if e["event"] == "stage" and e["stage"] == "figures"]
    assert [(e["lib"], e["message"]) for e in said] == [("merged", "1 kept for their figures; not read: LITRAG_FIGURES=off")]
    assert events[-1]["figures"] == [KEY]
    # nothing kept for its figures, nothing said of them
    events, _ = _merge_op(root, monkeypatch, capsys, sources=["threads"], name="New")
    assert [e for e in events if e["event"] == "stage" and e["stage"] == "figures"] == []


def test_a_read_an_earlier_merge_copied_that_found_no_plots_is_no_read_of_the_pdf_kept_now(threads, monkeypatch, capsys):
    """An earlier merge's copy of a read that found no plots holds no charts to keep: the PDF kept
    now is to be read, and with the figures off it is left for the figures op, not passed off as read."""
    monkeypatch.setenv("LITRAG_FIGURES", "off")
    root = threads["root"]
    projects.merge(root, ["threads"], "Merged")
    t, conn = _held(root)
    # as a merge made before one carried the PDF left a paper whose read found nothing: no charts, a read naming no file
    (t.papers_dir / KEPT).unlink()
    with conn:
        conn.execute("UPDATE papers SET figures_file = NULL WHERE key = ?", (KEY,))
        conn.execute("DELETE FROM charts WHERE paper = ?", (KEY,))
        conn.execute("UPDATE figure_reads SET file = NULL, plots = 0, read = 0 WHERE paper = ?", (KEY,))
    _read_into_rows(conn, KEY)
    events, asked = _merge_op(root, monkeypatch, capsys, sources=["threads"], into="merged")
    assert asked["figures"] == [] and events[-1]["figures"] == [KEY]
    assert [e["message"] for e in events if e["event"] == "stage" and e["stage"] == "figures"] == ["1 kept for their figures; not read: LITRAG_FIGURES=off"]
    assert conn.execute("SELECT figures_file FROM papers WHERE key = ?", (KEY,)).fetchone()[0] == KEPT
    assert not figures.is_read(conn, KEY)  # so the figures op, not forced, reads it
    conn.close()


def test_a_pdf_kept_beside_a_paper_the_target_has_still_to_read_is_not_copied_over(threads):
    """A paper the target holds with no file and no reading — its source's XML gone, no raw document
    — is new again to the next source, whose PDF is not copied over the one kept there with its read."""
    root, s = threads["root"], threads["threads"]
    gone = create_library(root, "Gone")
    conn = open_store(gone.store_path)
    _add(gone, conn, doi=SHARED, source=FIXTURES / "PMC11278924.xml", fmt="jats", raw=False)
    _read_into_rows(conn, KEY)
    _beside(gone, conn, KEY)
    conn.close()
    (gone.papers_dir / f"{safe_key(KEY)}.xml").unlink()
    (s.papers_dir / threads["name"]).write_bytes(PAGE.read_bytes() + b"\n% another copy")
    out = projects.merge(root, ["gone", "threads"], "Merged")
    assert (out["filed"], out["duplicates"]) == (2, 0)  # the second is new again: the target had no file of it, and no reading
    t, conn = _held(root)
    assert (t.papers_dir / KEPT).read_bytes() == PAGE.read_bytes()  # the first kept, as its read says
    assert tuple(conn.execute("SELECT file FROM figure_reads WHERE paper = ?", (KEY,)).fetchone()) == (KEPT,)
    assert sorted(p.name for p in t.papers_dir.glob("*.pdf")) == [KEPT]
    conn.close()


def test_charts_with_no_read_left_of_them_are_read_again_from_the_pdf_kept(threads):
    """Charts a read no longer stands for (a rebuild lost one of its figures, and the read went):
    no read to make the PDF's, so the PDF kept is read."""
    root = threads["root"]
    projects.merge(root, ["threads"], "Merged")
    t, conn = _held(root)
    (t.papers_dir / KEPT).unlink()
    with conn:
        conn.execute("UPDATE papers SET figures_file = NULL WHERE key = ?", (KEY,))
        conn.execute("DELETE FROM figure_reads WHERE paper = ?", (KEY,))
    _read_into_rows(conn, KEY)
    out = projects.merge(root, ["threads"], "", into="merged")
    assert (out["figures_kept"], out["keys_to_read_figures"]) == ([KEY], [KEY])
    conn.close()


def test_no_pdf_is_kept_beside_a_paper_the_target_reads_from_its_own_pdf(threads):
    root = threads["root"]
    pdfs = create_library(root, "Pdfs")
    conn = open_store(pdfs.store_path)
    _add(pdfs, conn, doi=SHARED, source=PAGE, fmt="pdf")
    conn.close()
    projects.merge(root, ["pdfs"], "Merged")
    out = projects.merge(root, ["threads"], "", into="merged")
    assert out["duplicates"] == 1 and (out["figures_kept"], out["keys_to_read_figures"], out["figures_not_kept"]) == ([], [], [])  # nothing to keep, nothing refused


def test_a_pdf_the_target_names_but_no_longer_has_gives_way_to_the_source_s(threads):
    root = threads["root"]
    projects.merge(root, ["bare"], "Merged")
    t, conn = _held(root)
    _read_into_rows(conn, KEY)
    with conn:
        conn.execute("UPDATE papers SET figures_file = 'gone.figures.pdf' WHERE key = ?", (KEY,))
    out = projects.merge(root, ["threads"], "", into="merged")
    assert (out["figures_kept"], out["keys_to_read_figures"]) == ([KEY], [KEY])
    assert conn.execute("SELECT figures_file FROM papers WHERE key = ?", (KEY,)).fetchone()[0] == KEPT and (t.papers_dir / KEPT).is_file()
    conn.close()


def test_a_paper_the_target_holds_by_another_key_takes_the_pdf_and_its_read_under_that_key(threads):
    """Filed in the target under the DOI in capitals, with no file and no reading: the paper new
    again to `threads`, which holds it in lower case, and its PDF and read go under the target's key."""
    root = threads["root"]
    upper = f"doi:{SHARED.upper()}"
    stub = create_library(root, "Stub")
    conn = open_store(stub.store_path)
    assert _add(stub, conn, doi=SHARED.upper(), source=FIXTURES / "PMC11278924.xml", fmt="jats", raw=False, tree=False) == upper
    conn.close()
    (stub.papers_dir / f"{safe_key(upper)}.xml").unlink()
    projects.merge(root, ["stub"], "Merged")
    out = projects.merge(root, ["threads"], "", into="merged")
    assert (out["filed"], out["duplicates"]) == (1, 0)
    t, conn = _held(root)
    name = f"{safe_key(upper)}.figures.pdf"
    assert conn.execute("SELECT figures_file FROM papers WHERE key = ?", (upper,)).fetchone()[0] == name and (t.papers_dir / name).read_bytes() == PAGE.read_bytes()
    assert [tuple(r) for r in conn.execute("SELECT paper, file FROM figure_reads")] == [(upper, name)]
    assert [r[0].split("#")[0] for r in conn.execute("SELECT figure FROM charts")] == [upper]
    conn.close()


def test_a_pdf_refused_is_named_by_the_key_its_source_holds_it_by(threads):
    """As a paper skipped is: the target's key may be another spelling of it."""
    root = threads["root"]
    upper_lib = create_library(root, "Upper")
    conn = open_store(upper_lib.store_path)
    _add(upper_lib, conn, doi=SHARED.upper(), source=FIXTURES / "PMC11278924.xml", fmt="jats")
    conn.close()
    projects.merge(root, ["upper"], "Merged")  # held under the DOI in capitals, titled with what page_paper.pdf does not print
    out = projects.merge(root, ["threads"], "", into="merged")
    assert out["figures_not_kept"] == [{"library": "threads", "paper": KEY, "reason": "its first pages name neither its DOI nor its title"}]


def test_a_read_goes_in_the_columns_both_stores_have(threads):
    """A source older than its target in some columns of its reads, newer in another."""
    root, s = threads["root"], threads["threads"]
    conn = open_store(s.store_path)
    conn.executescript("CREATE TABLE fr AS SELECT paper, reader, figures, plots, read, values_, seconds, at, 'x' AS newer FROM figure_reads; "
                       "DROP TABLE figure_reads; ALTER TABLE fr RENAME TO figure_reads;")
    conn.close()
    projects.merge(root, ["threads"], "Merged")
    _, conn = _held(root)
    assert tuple(conn.execute("SELECT file, unmatched, plots, at FROM figure_reads WHERE paper = ?", (KEY,)).fetchone()) == (None, None, 2, "the source's read")
    conn.close()


def test_a_pdf_paper_s_read_names_its_file_by_the_name_the_target_gives_it(tmp_path):
    root = tmp_path / "root"
    src = create_library(root, "Odd")
    conn = open_store(src.store_path)
    shutil.copy2(PAGE, src.papers_dir / "Odd Name.PDF")
    key = file_paper(conn, title="Odd", file="Odd Name.PDF", sha256=sha256_of(PAGE), fmt="pdf", doi="10.1/odd", pmid=None, pmcid=None, now="2026-09-01T00:00:00Z").key
    conn.execute("UPDATE papers SET status = 'parsed' WHERE key = ?", (key,))
    shutil.copy2(DOC, src.parsed_dir / f"{safe_key(key)}.docling.json")
    figures.ensure_schema(conn)
    with conn:
        conn.execute("INSERT INTO figure_reads(paper, reader, figures, plots, read, values_, seconds, at, file, unmatched) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (key, figures.READER, 1, 0, 0, 0, 1.0, "t", "Odd Name.PDF", 0))
    conn.close()
    projects.merge(root, ["odd"], "Merged")
    _, conn = _held(root)
    name = f"{safe_key(key)}.pdf"
    assert conn.execute("SELECT file FROM papers WHERE key = ?", (key,)).fetchone()[0] == name
    assert conn.execute("SELECT file FROM figure_reads WHERE paper = ?", (key,)).fetchone()[0] == name and figures.is_read(conn, key)
    conn.close()
