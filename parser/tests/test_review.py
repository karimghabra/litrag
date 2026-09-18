"""The review's own pages: the type's evidence, the citations, the XML path, the index.

The renderer writes HTML for a person to read, so what is tested is what a reader would look for —
that the winning source is marked, that every citation reaches the page with the entry it names,
that a paper which fails is listed as failed rather than dropped, and that a kind repeated hundreds
of times is gathered instead of printed out.
"""

import json
from pathlib import Path

from litrag_parser import review
from litrag_parser.citations import link_citations
from litrag_parser.tree import build_tree

FIXTURES = Path(__file__).parent / "fixtures"


def jats_tree():
    return build_tree(json.loads((FIXTURES / "PMC11278924.jats.docling.json").read_text("utf-8")), "doi:10.3390/mi15070851")


def test_publisher_from_the_doi_prefix():
    assert review.publisher_of("doi:10.1002/hsr2.72705") == "Wiley"
    assert review.publisher_of("doi:10.3390/mi15070851") == "MDPI"
    assert review.publisher_of("doi:10.1038/s41586-024-00001-0") == "Nature/Springer-Nature"
    assert review.publisher_of("doi:10.9999/unknown.1") == "10.9999"  # an unlisted prefix stands for itself
    assert review.publisher_of("pmid:12345") == "no DOI"
    assert review.publisher_of(None) == "no DOI"


def test_trust_bands():
    assert review.trust_band(0.97) == "high"
    assert review.trust_band(0.9) == "high"
    assert review.trust_band(0.5) == "fair"
    assert review.trust_band(0.2) == "low"
    assert review.trust_band(None) == "unscored"


def test_the_type_table_names_every_source_and_marks_the_winner():
    tree = jats_tree()
    xml = (FIXTURES / "PMC11278924.xml").read_bytes()
    kind = {"type": "research", "subtype": None, "source": "jats", "detail": "", "notes": []}
    table, shape = review._evidence_rows(tree, kind, xml, "research-article")
    assert "<th>source</th>" in table and "the type that implies" in table
    assert "a default bucket" in table  # MDPI's article-type is a bucket, and the table says so
    assert "<b>shape</b>" in table and "lanes" in table  # the shape's own sentence is shown
    assert "words of prose" in shape and "reporting statistics" in shape
    assert "{" not in table and "}" not in table


def test_the_sections_a_change_belongs_to():
    tree = jats_tree()
    _, owner = review._sections_index(tree)
    node = next(n for n in tree.walk() if n.type == "paragraph" and n.text)
    assert owner[node.node_id].startswith("doi:10.3390/mi15070851#")
    changes = [{"kind": "glyphs", "node_id": node.node_id}, {"kind": "joined", "node_id": None}]
    by_section = review._changes_by_section(changes, owner)
    assert by_section[owner[node.node_id]] == [changes[0]]
    assert by_section[None] == [changes[1]]


def test_every_linked_citation_reaches_the_page_with_its_entry():
    tree = jats_tree()
    refs, cites = link_citations(tree, (FIXTURES / "PMC11278924.xml").read_bytes())
    assert refs and cites
    counts = {"refs": len(refs), "citations": len(cites), "citing_nodes": 1, "cited_refs": 1}
    html = review._citations_block(tree, refs, cites, counts, review.unlinked_markers(tree, refs))
    assert html.count('<td class="marker">') == min(len(cites), review._CITE_ROWS)
    assert "<mark>" in html  # the marker is marked inside the sentence it sits in
    assert refs[0].text[:60] in html and "how often each entry is cited" in html
    assert "<details" in html and "None" not in html


def test_a_marker_that_names_no_entry_is_listed():
    tree = jats_tree()
    refs, _ = link_citations(tree)
    node = next(n for n in tree.walk() if n.type == "paragraph" and n.role != "references" and n.text)
    node.text = node.text + " A claim with no entry behind it [998, 999]."
    loose = review.unlinked_markers(tree, refs)
    assert any(marker == "[998, 999]" and missing == [998, 999] for _, marker, missing in loose)


def test_a_kind_repeated_is_gathered_not_printed():
    records = [{"kind": "bracket_numeric_xrefs", "stage": "xml", "before": f"{i}", "after": f"[{i}]",
                "why": "a numeric cross-reference the publisher prints bare"} for i in range(400)]
    html = review._change_list(records, prefix="x", group_all=True, cap=review._EXAMPLES)
    assert html.count('class="change"') == review._EXAMPLES
    assert "400 of them · showing 8" in html
    assert "and 392 more of this kind, not listed" in html
    assert "a numeric cross-reference the publisher prints bare" in html


def test_a_handful_of_changes_is_still_listed_one_by_one():
    records = [{"kind": "glyphs", "stage": "text", "before": "pH ¼ 7.4", "after": "pH = 7.4"} for _ in range(3)]
    html = review._change_list(records)
    assert html.count('class="change"') == 3 and 'class="group"' not in html


def test_the_xml_preparation_is_asked_for_its_log():
    raw = (FIXTURES / "PMC11278924.xml").read_bytes()
    prep = review.prepared_jats_records(raw)
    assert prep["available"] is True
    html = review._jats_block(prep)
    assert "What the XML preparation changed" in html
    if prep["records"]:  # the signature that keeps a log has landed
        assert prep["logged"] is True
        assert all(r.get("kind") for r in prep["records"])
        assert 'class="change"' in html


def test_the_xml_preparation_degrades_when_it_keeps_no_log(monkeypatch):
    from litrag_parser import jats_prep

    monkeypatch.setattr(jats_prep, "prepare_jats", lambda raw: raw, raising=True)
    prep = review.prepared_jats_records(b"<article/>")
    assert prep == {"available": True, "records": [], "changed": False, "logged": False, "before": 10, "after": 10}
    assert "handed to Docling as it came" in review._jats_block(prep)


def test_the_page_strip_marks_the_pages_that_changed():
    images = {1: {"file": "pages/page-001.jpg"}, 2: {"file": "pages/page-002.jpg"}}
    strip = review._nav_strip(images, {2}, jats_tree(), {})
    assert 'href="#page-1"' in strip and 'href="#page-2"' in strip
    assert strip.count('class="changed"') == 1


def test_a_paper_with_no_pages_gets_its_sections_to_jump_to():
    tree = jats_tree()
    strip = review._nav_strip({}, set(), tree, {})
    assert "#sec-" in strip and "#page-" not in strip


def _row(**over):
    row = {"library": "lib", "key": "doi:10.3390/mi15070851", "title": "A paper", "format": "jats",
           "publisher": "MDPI", "pages": 0, "type": "research", "subtype": None, "type_source": "jats",
           "confidence": 0.95, "band": "high", "changes": 4, "by_stage": {"text": 4}, "refs": 61,
           "citations": 120, "unlinked": 0, "errors": 0, "warnings": 1, "failed": False, "error": None,
           "report": "lib--doi/index.html"}
    row.update(over)
    return row


def test_a_failed_paper_is_named_in_the_index_not_dropped(tmp_path):
    failed = review.failed_row(Path("/libs/held-out-xml"), {"key": "doi:10.1016/j.x.2026.1", "format": "jats"},
                               ValueError("no reference list could be read"))
    assert failed["failed"] is True and failed["publisher"] == "Elsevier"
    path = review.render_index([_row(), failed], tmp_path)
    html = path.read_text("utf-8")
    assert "no reference list could be read" in html
    assert 'data-state="failed"' in html and 'data-state="read"' in html
    assert "1 failed" in html
    rows = json.loads((tmp_path / "index.json").read_text("utf-8"))
    assert [r["key"] for r in rows if r["failed"]] == ["doi:10.1016/j.x.2026.1"]


def test_the_index_carries_its_filters_its_sorting_and_its_publishers(tmp_path):
    path = review.render_index([_row(), _row(key="doi:10.1002/x.1", publisher="Wiley", format="pdf", pages=9)], tmp_path)
    html = path.read_text("utf-8")
    for control in ("f-q", "f-library", "f-format", "f-type", "f-band", "f-publisher", "f-state", "f-min", "f-clear"):
        assert f'id="{control}"' in html
    assert 'data-key="changes" data-numeric="1"' in html
    assert "By publisher" in html and ">Wiley<" in html and ">MDPI<" in html
    assert "cdn" not in html.lower() and "<script>" in html  # self-contained: no library is fetched


def test_a_block_that_raises_does_not_take_the_paper_with_it():
    def explode() -> str:
        raise RuntimeError("the reference list is not a list")

    html = review._card("Every citation", explode)
    assert "this block could not be built" in html and "the reference list is not a list" in html
