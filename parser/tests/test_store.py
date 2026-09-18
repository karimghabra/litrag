import json
from pathlib import Path

from litrag_parser.store import file_paper, node, open_store, paper_tree, run_select, save_tree, section, siblings
from litrag_parser.tree import build_tree

FIXTURES = Path(__file__).parent / "fixtures"


def test_filing_is_idempotent_by_doi_then_hash(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    a = file_paper(conn, title="x", file="a.pdf", sha256="aa", fmt="pdf", doi="10.1/abc", pmid=None, pmcid=None, now="t")
    b = file_paper(conn, title="x", file="b.pdf", sha256="bb", fmt="pdf", doi="10.1/abc", pmid=None, pmcid=None, now="t")
    c = file_paper(conn, title="y", file="c.pdf", sha256="cc", fmt="pdf", doi=None, pmid=None, pmcid=None, now="t")
    d = file_paper(conn, title="y", file="c.pdf", sha256="cc", fmt="pdf", doi=None, pmid=None, pmcid=None, now="t")
    assert a.key == "doi:10.1/abc" and not a.existed
    assert b.key == a.key and b.existed
    assert c.key.startswith("sha:") and not c.existed and d.key == c.key and d.existed
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 2


def test_a_hash_stub_gives_way_to_the_identified_paper(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    stub = file_paper(conn, title="stub", file="s.xml", sha256="s1", fmt="jats", doi=None, pmid=None, pmcid=None, now="t")
    real = file_paper(conn, title="real", file="r.pdf", sha256="r1", fmt="pdf", doi="10.1/x", pmid=None, pmcid=None, now="t")
    again = file_paper(conn, title="stub", file="s.xml", sha256="s1", fmt="jats", doi="10.1/x", pmid=None, pmcid=None, now="t")
    assert stub.key.startswith("sha:") and again.key == real.key and again.existed
    assert [r["key"] for r in conn.execute("SELECT key FROM papers")] == [real.key]
    row = conn.execute("SELECT file, sha256, format FROM papers WHERE key = ?", (real.key,)).fetchone()
    assert (row["file"], row["sha256"], row["format"]) == ("r.pdf", "r1", "pdf")  # gaps filled, nothing moved backwards


def test_tree_roundtrips_through_rows(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    key = file_paper(conn, title="x", file="a.pdf", sha256="aa", fmt="pdf", doi="10.3390/mi15070851", pmid=None, pmcid=None, now="t").key
    tree = build_tree(json.loads((FIXTURES / "PMC11278924.docling.json").read_text()), key)
    n = save_tree(conn, key, tree, parser="test", parsed_at="t", seconds=1.0)
    assert n == sum(1 for _ in tree.walk())
    # saving again replaces, never duplicates
    assert save_tree(conn, key, tree, parser="test", parsed_at="t", seconds=1.0) == n
    assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == n

    got = paper_tree(conn, key)
    assert got["paper"]["status"] == "parsed" and got["paper"]["has_methods"] == 1
    assert got["root"]["node_id"] == key
    assert len(got["pages"]) == 14
    tops = [c for c in got["root"]["children"] if c["type"] == "section"]
    assert [c["role"] for c in tops if c["heading"] != "Front matter"][:5] == ["abstract", "introduction", "methods", "results", "discussion"]

    methods = section(conn, key, "methods")
    assert methods and all(m["role"] == "methods" for m in methods)
    first_sub = next(m for m in methods if m["type"] == "section" and m["level"] == 2)
    sibs = siblings(conn, first_sub["node_id"])
    assert [s["heading"][:4] for s in sibs] == ["2.1.", "2.2.", "2.3.", "2.4.", "2.5.", "2.6.", "2.7.", "2.8."]
    assert node(conn, first_sub["node_id"])["ancestry"] == ["2. Materials and Methods"]

    rows = run_select(conn, "select role, count(*) n from nodes where type = 'paragraph' group by role order by n desc")
    assert rows["columns"] == ["role", "n"] and rows["rows"][0]["role"] in ("results", "methods", "references")
    fts = run_select(conn, "select count(*) c from nodes_fts where nodes_fts match 'genipin'")
    assert fts["rows"][0]["c"] > 0


def test_sql_is_read_only_and_one_statement(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    import pytest

    with pytest.raises(ValueError):
        run_select(conn, "delete from papers")
    with pytest.raises(ValueError):
        run_select(conn, "select 1; select 2")
    assert run_select(conn, "select 'a;b' as s")["rows"] == [{"s": "a;b"}]


def test_judgments_are_rows(tmp_path):
    from litrag_parser.store import judgment, save_judgment

    conn = open_store(tmp_path / "store.sqlite")
    file_paper(conn, title="t", file="x.pdf", sha256="s", fmt="pdf", doi="10.1/x", pmid=None, pmcid=None, now="2026-09-11T00:00:00Z")
    assert judgment(conn, "doi:10.1/x", "abc") is None
    save_judgment(conn, "doi:10.1/x", "abc", True, "qwen3:14b", "2026-09-11T00:00:00Z")
    save_judgment(conn, "doi:10.1/x", "def", False, "qwen3:14b", "2026-09-11T00:00:00Z")
    assert judgment(conn, "doi:10.1/x", "abc") == 1 and judgment(conn, "doi:10.1/x", "def") == 0
    save_judgment(conn, "doi:10.1/x", "abc", False, "qwen3:14b", "2026-09-11T00:01:00Z")  # a second verdict replaces the first
    assert judgment(conn, "doi:10.1/x", "abc") == 0
