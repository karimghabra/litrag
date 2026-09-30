"""Who wrote the paper, where and when: the JATS file's contributor group first, else Europe
PMC's record, fetched once at ingest — columns a rebuild keeps, nothing inferred."""

import json
import sqlite3
from pathlib import Path

from litrag_parser import record
from litrag_parser.record import jats_authors, jats_journal, lookup_record
from litrag_parser.store import open_store, set_record

FIXTURES = Path(__file__).parent / "fixtures"

RSC = b"""<article><front><article-meta><contrib-group>
<contrib contrib-type="author"><name><surname>Trung</surname><given-names>Do Dinh</given-names></name><xref ref-type="aff" rid="affa">a</xref></contrib>
<contrib contrib-type="author" corresp="yes"><name><surname>Thi</surname><given-names>Le Anh</given-names></name><xref ref-type="aff" rid="affe">e</xref><xref ref-type="aff" rid="afff">f</xref></contrib>
<contrib contrib-type="author"><collab>The Consortium</collab></contrib>
<contrib contrib-type="editor"><name><surname>Nobody</surname></name></contrib>
</contrib-group>
<aff id="affa"><label>a</label>Institute of Tropical Durability, Hanoi, Vietnam</aff>
<aff id="affe"><label>e</label>Duy Tan University, Da Nang, Vietnam</aff>
<aff id="afff"><label>f</label>Faculty of Natural Sciences, Duy Tan University</aff>
</article-meta></front></article>"""


def test_the_files_contributors_with_their_affiliations():
    xml = (FIXTURES / "PMC11278924.xml").read_bytes()
    authors = jats_authors(xml)
    assert len(authors) >= 3 and all(a["name"] and a["name"][0].isupper() for a in authors)
    assert authors[0]["affiliations"] and all(isinstance(a["corresponding"], bool) for a in authors)
    assert jats_journal(xml) == ("Micromachines", "2024")


def test_labels_inside_affiliations_collaborations_and_editors_left_out():
    got = jats_authors(RSC)
    assert [a["name"] for a in got] == ["Do Dinh Trung", "Le Anh Thi", "The Consortium"]
    assert got[0]["affiliations"] == ["Institute of Tropical Durability, Hanoi, Vietnam"] and not got[0]["corresponding"]
    assert got[1]["affiliations"] == ["Duy Tan University, Da Nang, Vietnam", "Faculty of Natural Sciences, Duy Tan University"] and got[1]["corresponding"]
    assert got[2]["affiliations"] == []
    assert jats_authors(b"<article/>") == [] and jats_authors(None) == [] and jats_journal(None) == (None, None) and jats_journal(b"<article/>") == (None, None)


def test_the_record_by_doi_and_nothing_when_offline(monkeypatch):
    class Resp:
        def __init__(self, body: bytes):
            self.body = body

        def read(self) -> bytes:
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen: list[str] = []

    def fake(url, timeout=0):
        seen.append(url)
        return Resp(json.dumps({"resultList": {"result": [{"authorString": "Trung DD, Duong PV, Thi LA.", "journalTitle": "RSC Adv", "pubYear": "2026", "pubType": "research-article; Journal Article"}]}}).encode())

    monkeypatch.setattr(record.urllib.request, "urlopen", fake)
    got = lookup_record("10.1039/d6ra07899k")
    assert got == {
        "pub_types": ["research-article", "Journal Article"],
        "authors": [{"name": n, "affiliations": [], "corresponding": False} for n in ("Trung DD", "Duong PV", "Thi LA")],
        "journal": "RSC Adv",
        "year": "2026",
    }
    assert len(seen) == 1 and "DOI" in seen[0] and "d6ra07899k" in seen[0]
    assert lookup_record(pmid="123") is not None and "EXT_ID" in seen[1]
    assert lookup_record() is None and len(seen) == 2

    def down(url, timeout=0):
        raise OSError("offline")

    monkeypatch.setattr(record.urllib.request, "urlopen", down)
    assert lookup_record("10.1/x") is None


def test_the_record_fills_gaps_and_the_file_overrides(tmp_path):
    old = tmp_path / "store.sqlite"
    c = sqlite3.connect(old)  # a store from before the record was a column grows it
    c.execute("CREATE TABLE papers (key TEXT PRIMARY KEY, doi TEXT, pmid TEXT, pmcid TEXT, title TEXT NOT NULL, file TEXT, sha256 TEXT, format TEXT, pages INTEGER, status TEXT NOT NULL DEFAULT 'queued', error TEXT, parser TEXT, added_at TEXT NOT NULL, parsed_at TEXT, seconds REAL, has_methods INTEGER)")
    c.execute("INSERT INTO papers(key, title, added_at) VALUES ('p', 't', '2026-01-01T00:00:00Z')")
    c.commit()
    c.close()
    conn = open_store(old)
    set_record(conn, "p", pub_types=["Journal Article"], authors=[{"name": "Trung DD", "affiliations": [], "corresponding": False}], journal="RSC Adv", year="2026")
    row = conn.execute("SELECT pub_types, authors, journal, year FROM papers WHERE key = 'p'").fetchone()
    assert row["pub_types"] == "Journal Article" and json.loads(row["authors"])[0]["name"] == "Trung DD" and (row["journal"], row["year"]) == ("RSC Adv", "2026")
    set_record(conn, "p", journal="Other", year="2020")  # the record fills gaps only
    row = conn.execute("SELECT journal, year FROM papers WHERE key = 'p'").fetchone()
    assert (row["journal"], row["year"]) == ("RSC Adv", "2026")
    set_record(conn, "p", authors=[{"name": "Do Dinh Trung", "affiliations": ["Hanoi"], "corresponding": False}], overwrite=True)  # the file's word replaces
    assert json.loads(conn.execute("SELECT authors FROM papers WHERE key = 'p'").fetchone()["authors"])[0]["name"] == "Do Dinh Trung"
    set_record(conn, "p")  # nothing to say: nothing written
    set_record(conn, "p", authors=[], journal="", overwrite=True)  # nothing either: an empty word never erases
    row = conn.execute("SELECT authors, journal FROM papers WHERE key = 'p'").fetchone()
    assert json.loads(row["authors"])[0]["name"] == "Do Dinh Trung" and row["journal"] == "RSC Adv"
