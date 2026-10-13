import json
from pathlib import Path

from litrag_parser.store import file_paper, find_paper, node, open_store, paper_tree, run_select, save_tree, section, siblings
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


def test_a_doi_in_another_case_is_the_same_paper_and_filing_says_what_met_it(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    first = file_paper(conn, title="x", file="a.pdf", sha256="aa", fmt="pdf", doi="10.1/ABC", pmid=None, pmcid=None, now="t1")
    again = file_paper(conn, title="x", file="b.pdf", sha256="bb", fmt="pdf", doi="10.1/abc", pmid=None, pmcid=None, now="t2")
    assert (first.key, first.existed, first.by) == ("doi:10.1/ABC", False, None)
    assert (again.key, again.existed, again.by) == ("doi:10.1/ABC", True, "doi")
    assert [tuple(r) for r in conn.execute("SELECT key, doi FROM papers")] == [("doi:10.1/ABC", "10.1/ABC")]  # spelled as first filed
    # each step says itself: a PMID, then the file's hash; a DOI before a PMID that names another paper
    p = file_paper(conn, title="y", file="c.pdf", sha256="cc", fmt="pdf", doi=None, pmid="123", pmcid=None, now="t3")
    assert (p.key, p.existed, p.by) == ("pmid:123", False, None)
    by_pmid = file_paper(conn, title="y", file="d.pdf", sha256="dd", fmt="pdf", doi="10.2/new", pmid="123", pmcid=None, now="t4")
    assert (by_pmid.key, by_pmid.existed, by_pmid.by) == ("pmid:123", True, "pmid")
    by_doi = file_paper(conn, title="y", file="e.pdf", sha256="ee", fmt="pdf", doi="10.1/Abc", pmid="123", pmcid=None, now="t5")
    assert (by_doi.key, by_doi.by) == ("doi:10.1/ABC", "doi")
    h = file_paper(conn, title="z", file="f.pdf", sha256="ff", fmt="pdf", doi=None, pmid=None, pmcid=None, now="t6")
    by_sha = file_paper(conn, title="z", file="f.pdf", sha256="ff", fmt="pdf", doi=None, pmid=None, pmcid=None, now="t7")
    assert (h.existed, h.by) == (False, None) and (by_sha.key, by_sha.existed, by_sha.by) == (h.key, True, "sha")


def test_a_doi_held_in_several_cases_meets_its_own_bytes_or_the_one_filed_first(tmp_path):
    """A store from before DOIs were met in any case may hold one in several spellings, each a
    paper read: a file of one of them meets that one, and no other is lost as its stub; any other
    file meets the one filed first, whichever spelling it comes in."""
    conn = open_store(tmp_path / "store.sqlite")
    # filed first is Ab: first neither by row nor by key, in either direction
    held = (("doi:10.1/AB", "2026-01-02"), ("doi:10.1/Ab", "2026-01-01"), ("doi:10.1/ab", "2026-01-03"))
    for key, at in held:
        conn.execute("INSERT INTO papers(key, doi, title, sha256, status, added_at) VALUES (?,?,'t',?,'parsed',?)", (key, key[4:], f"bytes of {key}", at))
        conn.execute("INSERT INTO nodes(node_id, paper, ordinal, depth, type, label, role, ancestry, text) VALUES (?,?,0,0,'paragraph','text','other','[]','read')", (f"{key}#0", key))
    conn.commit()
    for spelling in ("10.1/ab", "10.1/AB", "10.1/aB"):
        f = file_paper(conn, title="t", file="", sha256="other bytes", fmt="pdf", doi=spelling, pmid=None, pmcid=None, now="2026-01-04")
        assert (f.key, f.by) == ("doi:10.1/Ab", "doi")
    for key, _ in held:  # each one's own file, dropped again
        f = file_paper(conn, title="t", file="", sha256=f"bytes of {key}", fmt="pdf", doi=key[4:], pmid=None, pmcid=None, now="2026-01-04")
        assert (f.key, f.existed, f.by) == (key, True, "doi")
    assert [tuple(r) for r in conn.execute("SELECT paper, COUNT(*) FROM nodes GROUP BY paper ORDER BY paper")] == [(k, 1) for k, _ in sorted(held)]


def test_finding_a_paper_meets_the_one_filing_meets_and_writes_nothing(tmp_path):
    """`find_paper` is filing's own lookup, read only: at each step it names the paper and the
    identifier `file_paper` would, and changes no row — not the gaps filing fills, nor the stub of
    the same bytes filing drops."""
    conn = open_store(tmp_path / "store.sqlite")
    assert find_paper(conn, doi="10.1/abc", pmid=None, sha256="aa") is None
    file_paper(conn, title="x", file="a.pdf", sha256="aa", fmt="pdf", doi="10.1/ABC", pmid=None, pmcid=None, now="t1")
    file_paper(conn, title="y", file="c.pdf", sha256="cc", fmt="pdf", doi=None, pmid="123", pmcid=None, now="t2")
    file_paper(conn, title="z", file="f.pdf", sha256="ff", fmt="pdf", doi=None, pmid=None, pmcid=None, now="t3")
    stub = file_paper(conn, title="s", file="s.pdf", sha256="ss", fmt="pdf", doi=None, pmid=None, pmcid=None, now="t4").key
    conn.commit()
    asks = [
        ({"doi": "10.1/abc", "pmid": None, "sha256": "bb"}, ("doi:10.1/ABC", "doi")),  # a DOI in another case
        ({"doi": "10.2/new", "pmid": "123", "sha256": "dd"}, ("pmid:123", "pmid")),  # a DOI held by none, then a PMID
        ({"doi": None, "pmid": None, "sha256": "ff"}, ("sha:ff", "sha")),  # the file's hash
        ({"doi": "10.1/Abc", "pmid": "123", "sha256": "ss"}, ("doi:10.1/ABC", "doi")),  # a DOI first, and the bytes of a stub filing would drop
    ]
    for ask, meets in asks:
        changes = conn.total_changes
        found = find_paper(conn, **ask)
        assert conn.total_changes == changes  # read only
        filed = file_paper(conn, title="t", file="", fmt="pdf", pmcid="PMC1", now="t9", **ask)
        assert found == meets == (filed.key, filed.by) and filed.existed
    assert conn.execute("SELECT COUNT(*) FROM papers WHERE key = ?", (stub,)).fetchone()[0] == 0  # filing dropped it; finding had not


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


def test_a_silence_keeps_what_it_nearly_decided(tmp_path):
    """`role` stays the asserted lane, `other` still means the reader declined — and the near
    miss is a row beside it. A reader that abstains without saying what it nearly decided
    cannot be improved by anyone, including itself."""
    from litrag_parser.store import open_store, paper_tree, save_tree
    from litrag_parser.tree import Node, Page, Tree

    section = Node(node_id="s", parent="root", ordinal=1, depth=1, type="section",
                   label="section_header", level=1, role="other", heading="Neurophysiology",
                   ancestry=[], text="", page=1, bbox=None, self_ref=None,
                   guess="methods", confidence=0.62,
                   reasons={"vocabulary": "other", "catalogue": "other", "embedder": "methods",
                            "blocks": "methods"})
    root = Node(node_id="root", parent=None, ordinal=0, depth=0, type="document",
                label="document", level=None, role="other", heading=None, ancestry=[], text="",
                page=None, bbox=None, self_ref=None, children=[section])
    tree = Tree(title="t", pages=[Page(page_no=1, width=612.0, height=792.0)], root=root,
                roles={}, has_methods=False)

    conn = open_store(tmp_path / "store.sqlite")
    conn.execute("INSERT INTO papers(key, title, added_at) VALUES ('k', 't', '2026-01-01')")
    conn.commit()
    save_tree(conn, "k", tree, parser="test", parsed_at="2026-01-01", seconds=0.0)

    got = conn.execute("SELECT role, guess, confidence, reasons FROM nodes WHERE node_id = 's'").fetchone()
    assert got["role"] == "other", "the reader still declines: coverage is what abstaining costs"
    assert got["guess"] == "methods" and got["confidence"] == 0.62
    assert json.loads(got["reasons"])["embedder"] == "methods"

    back = paper_tree(conn, "k")
    kid = back["root"]["children"][0]
    assert kid["role"] == "other" and kid["guess"] == "methods"
    assert kid["reasons"]["blocks"] == "methods"
    conn.close()
