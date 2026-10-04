"""projects.py: a project's summary, its description, and one library made of several — each
paper filed once, its raw document and stored answers carried, the sources left as they were."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from litrag_parser import acquire, projects
from litrag_parser.library import create_library, open_library, parsed_papers, safe_key
from litrag_parser.store import file_paper, open_store, save_judgment, save_tree, set_record, set_type, sha256_of
from litrag_parser.tree import build_tree

FIXTURES = Path(__file__).parent / "fixtures"
DOC = FIXTURES / "PMC11278924.docling.json"
SHARED = "10.3390/mi15070851"


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


def test_merge_refuses_what_it_cannot_do(two):
    root = two["root"]
    with pytest.raises(ValueError, match="no such library"):
        projects.merge(root, ["nope"], "X")
    with pytest.raises(ValueError, match="already exists"):
        projects.merge(root, ["collagen-a"], "Collagen B")
    with pytest.raises(ValueError, match="into itself"):
        projects.merge(root, ["collagen-a", "collagen-b"], "", into="collagen-b")


def test_merge_carries_the_labels_a_person_made(two):
    """A person's finding→method labels are not derived from the paper: they travel with it, a
    duplicate's as well, under the key the target holds it by; merging again adds nothing."""
    root, ka, kb = two["root"], two["ka"], two["kb_same"]
    rows = {"a": (ka, f"{ka}#section-5#paragraph-1", "The aligned threads were stronger.", f"{ka}#section-4#section-6", "2.6. Mechanical Assessment", "yes"),
            "b": (kb, f"{kb}#section-5#paragraph-2", "The random threads were weaker.", "", "", "none")}
    for side, row in rows.items():
        conn = open_store(two[side].store_path)
        with conn:
            conn.execute("INSERT INTO link_labels(paper, finding, finding_text, method, method_heading, verdict, by, at) VALUES (?,?,?,?,?,?,'Karim','t')", row)
        conn.close()
    projects.merge(root, ["collagen-a", "collagen-b"], "Collagen")
    projects.merge(root, ["collagen-a", "collagen-b"], "Collagen", into="collagen")
    conn = open_store(open_library(root, "collagen").store_path)
    got = sorted(tuple(r) for r in conn.execute("SELECT paper, finding, method, verdict, by FROM link_labels"))
    conn.close()
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
