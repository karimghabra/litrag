"""acquire.py against a canned Europe PMC and NCBI on 127.0.0.1: search, candidates once, XML
first (Europe PMC's, then NCBI's), then the bulk PDF, else needs-pdf. Nothing here touches the
network."""

import io
import json
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from litrag_parser import acquire
from litrag_parser.library import create_library
from litrag_parser.store import file_paper, open_store

FIXTURES = Path(__file__).parent / "fixtures"

#: Shaped like a live `resultType=core` answer (fields checked against Europe PMC, 2026-09):
#: a core hit has `journalInfo.journal.title` and no `journalTitle`, and `pubTypeList.pubType`.
CORE = {
    "version": "6.9",
    "hitCount": 12140,
    "nextCursorMark": "AoIIQNLlNig1NjQxODkzOQ==",
    "request": {"queryString": "hydrogel cartilage", "resultType": "core", "cursorMark": "*", "pageSize": 3},
    "resultList": {"result": [
        {"id": "42710039", "source": "MED", "pmid": "42710039", "doi": "10.1021/ACSBIOMATERIALS.6c00857",
         "title": "Synergistic Tuning of Pore Architecture in a COL-HA-PVA Hydrogel.",
         "authorString": "Chen P, Lu W, Wang H.", "pubYear": "2026",
         "journalInfo": {"yearOfPublication": 2026, "journal": {"title": "ACS biomaterials science & engineering"}},
         "abstractText": "Engineering hydrogels that <i>simultaneously</i> provide pores.",
         "pubTypeList": {"pubType": ["Journal Article"]},
         "isOpenAccess": "N", "inEPMC": "N", "inPMC": "N", "hasPDF": "N", "citedByCount": 0},
        {"id": "42644960", "source": "MED", "pmid": "42644960", "pmcid": "PMC13512485", "doi": "10.3390/gels12080715",
         "title": "MSC-Hydrogel Composite Systems for Knee Cartilage Repair: A Systematic Review.",
         "authorString": "Raimagambetov Y, Suiindik B", "pubYear": "2026",
         "journalInfo": {"journal": {"title": "Gels (Basel, Switzerland)"}},
         "abstractText": "<b>Background:</b> MSC-hydrogel composite systems.",
         "pubTypeList": {"pubType": ["review-article", "Review", "Journal Article"]},
         "isOpenAccess": "Y", "inEPMC": "Y", "inPMC": "Y", "hasPDF": "Y", "citedByCount": 3},
        {"id": "PPR1", "source": "PPR", "doi": "10.1101/2026.01.01.1", "pmcid": "pmc13572800",
         "title": "&lt;i&gt;In Vivo&lt;/i&gt; cartilage &amp;amp; bone",
         "authorString": "Yang G", "pubYear": "2026", "journalTitle": "bioRxiv",
         "pubType": "preprint", "isOpenAccess": "Y", "inEPMC": "N", "inPMC": "N", "hasPDF": "Y", "citedByCount": "2"},
    ]},
}


class Canned:
    """A tiny Europe PMC: routes by path, every request remembered."""

    def __init__(self):
        self.routes: dict[str, tuple[int, str, bytes]] = {}
        self.seen: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.seen.append(self.path)
                path = urllib.parse.urlsplit(self.path).path
                status, ctype, body = outer.routes.get(path, (404, "text/html", b"<html>not found</html>"))
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def json(self, path, obj, status=200):
        self.routes[path] = (status, "application/json", json.dumps(obj).encode())

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def epmc(monkeypatch):
    c = Canned()
    monkeypatch.setenv("LITRAG_EPMC_URL", f"{c.url}/rest")
    monkeypatch.setenv("LITRAG_EPMC_PDF_URL", f"{c.url}/oa")
    monkeypatch.setenv("LITRAG_NCBI_URL", f"{c.url}/ncbi")
    monkeypatch.delenv("LITRAG_NCBI_EMAIL", raising=False)
    monkeypatch.delenv("LITRAG_NCBI_API_KEY", raising=False)
    monkeypatch.setattr(acquire, "NCBI_GAP", 0)
    yield c
    c.close()


def _ncbi_asked(seen: list[str]) -> list[str]:
    """The PMCIDs (as E-utilities' bare numbers) NCBI was asked for, in order."""
    return [urllib.parse.parse_qs(urllib.parse.urlsplit(p).query)["id"][0] for p in seen if p.startswith("/ncbi/efetch.fcgi")]


def _articleset(article: bytes) -> bytes:
    """An article as `efetch` sends one: inside a `<pmc-articleset>`, under its DOCTYPE."""
    return (b'<?xml version="1.0"  ?><!DOCTYPE pmc-articleset PUBLIC "-//NLM//DTD ARTICLE SET 2.0//EN" '
            b'"https://dtd.nlm.nih.gov/ncbi/pmc/articleset/nlm-articleset-2.0.dtd"><pmc-articleset>' + article + b"</pmc-articleset>")


@pytest.fixture
def lib(tmp_path):
    return create_library(tmp_path / "root", "Hydrogels")


def _zip(pmcid: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("supplement_S1.pdf", b"%PDF-1.4 the supplement, larger " + b"x" * 400)
        z.writestr(f"{pmcid}.pdf", b"%PDF-1.4 the paper itself")
        z.writestr("fig1.jpg", b"\xff\xd8")
    return buf.getvalue()


def test_search_normalises_a_core_answer(epmc):
    epmc.json("/rest/search", CORE)
    page = acquire.search("hydrogel cartilage", page_size=3)
    assert page["total"] == 12140 and page["next_cursor"] == "AoIIQNLlNig1NjQxODkzOQ=="
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(epmc.seen[-1]).query)
    assert q["resultType"] == ["core"] and q["format"] == ["json"] and q["cursorMark"] == ["*"] and q["pageSize"] == ["3"]
    closed, review, preprint = page["hits"]
    assert closed == {
        "source": "europepmc", "epmc_source": "MED", "id": "42710039", "pmid": "42710039", "pmcid": None,
        "doi": "10.1021/acsbiomaterials.6c00857", "title": "Synergistic Tuning of Pore Architecture in a COL-HA-PVA Hydrogel",
        "authors": "Chen P, Lu W, Wang H", "journal": "ACS biomaterials science & engineering", "year": "2026",
        "abstract": "Engineering hydrogels that simultaneously provide pores.", "is_open_access": False, "in_pmc": False,
        "in_epmc": False, "has_pdf": False, "has_xml": False, "cited_by": 0, "pub_types": ["Journal Article"],
    }
    assert review["has_xml"] and review["has_pdf"] and review["is_open_access"] and review["in_pmc"]
    assert review["pub_types"] == ["review-article", "Review", "Journal Article"] and review["abstract"] == "Background: MSC-hydrogel composite systems."
    # a lite-shaped hit still reads: journalTitle, a pubType string; not in Europe PMC, so no XML
    assert preprint["journal"] == "bioRxiv" and preprint["pub_types"] == ["preprint"] and preprint["cited_by"] == 2
    assert preprint["pmcid"] == "PMC13572800" and preprint["has_xml"] is False
    assert preprint["title"] == "In Vivo cartilage & bone"


def test_last_page_has_no_cursor(epmc):
    epmc.json("/rest/search", {"hitCount": 0, "nextCursorMark": "*", "resultList": {"result": []}})
    assert acquire.search("nothing at all") == {"hits": [], "next_cursor": None, "total": 0}


def test_search_that_cannot_be_asked_raises_a_named_error(epmc):
    epmc.json("/rest/search", {"error": "boom"}, status=500)
    with pytest.raises(acquire.AcquireError, match="500"):
        acquire.search("x")


def test_record_search_twice_adds_once(epmc, lib):
    epmc.json("/rest/search", CORE)
    conn = open_store(lib.store_path)
    page = acquire.search("hydrogel cartilage")
    first = acquire.record_search(lib, conn, "hydrogel cartilage", page["hits"], total=page["total"])
    assert first["added"] == 3 and first["seen"] == 0
    acquire.stage(conn, [first["cand_ids"][1]])
    second = acquire.record_search(lib, conn, "cartilage hydrogel", page["hits"], total=page["total"])
    assert second["added"] == 0 and second["seen"] == 3 and second["cand_ids"] == first["cand_ids"]
    rows = acquire.candidates(conn)
    assert len(rows) == 3
    assert [r["status"] for r in rows] == ["found", "staged", "found"]  # one seen before keeps its status
    assert rows[0]["query"] == "hydrogel cartilage"  # and the search that first found it
    assert rows[1]["pub_types"] == "review-article; Review; Journal Article" and rows[1]["has_xml"] == 1
    assert acquire.candidates(conn, query="hydrogel cartilage") == rows
    m = json.loads(lib.manifest_path.read_text("utf-8"))
    assert [(q["query"], q["total"], q["added"]) for q in m["queries"]] == [("hydrogel cartilage", 12140, 3), ("cartilage hydrogel", 12140, 0)]
    assert list(m) == sorted(m)  # written the way create_library writes it
    conn.close()


def test_reconcile_marks_what_the_library_holds(lib):
    conn = open_store(lib.store_path)
    acquire.ensure_schema(conn)
    hits = [acquire.normalise_hit(h) for h in CORE["resultList"]["result"]]
    acquire.record_search(lib, conn, "q", hits)
    # the paper as the worker files it: the DOI as the file prints it, in its own case
    f = file_paper(conn, title="t", file="x.xml", sha256="ab" * 32, fmt="jats", doi="10.1021/ACSBiomaterials.6C00857", pmid=None, pmcid=None, now="t")
    file_paper(conn, title="t2", file="y.pdf", sha256="cd" * 32, fmt="pdf", doi=None, pmid=None, pmcid="PMC13512485", now="t")
    conn.commit()
    assert acquire.reconcile(conn) == 2
    rows = {r["pmid"] or r["pmcid"]: r for r in acquire.candidates(conn)}
    assert rows["42710039"]["status"] == "ingested" and rows["42710039"]["paper_key"] == f.key
    assert rows["42644960"]["status"] == "ingested" and rows["42644960"]["paper_key"] == "pmcid:PMC13512485"
    assert rows["PMC13572800"]["status"] == "found"
    assert acquire.reconcile(conn) == 0  # twice changes nothing
    assert acquire.dismiss(conn, [rows["42710039"]["cand_id"]])["changed"] == []  # a held paper stays held
    conn.close()


def _cands(lib, conn):
    hits = [
        {"pmcid": "PMC100001", "doi": "10.1/xml", "title": "xml", "has_xml": True, "is_open_access": True, "has_pdf": True},
        {"pmcid": "PMC100002", "doi": "10.1/fallthrough", "title": "500 then zip", "has_xml": True, "is_open_access": True, "has_pdf": True},
        {"pmcid": "PMC100003", "pmid": "333", "doi": "10.1/nothing", "title": "404 twice", "has_xml": True, "is_open_access": True, "has_pdf": True},
        {"pmid": "444", "doi": "10.1/closed", "title": "closed", "has_xml": False, "is_open_access": False, "has_pdf": False},
    ]
    return acquire.record_search(lib, conn, "q", hits)["cand_ids"]


def test_fetch_xml_first_then_the_bulk_pdf_else_needs_pdf(epmc, lib):
    xml = (FIXTURES / "PMC11278924.xml").read_bytes()
    epmc.routes["/rest/PMC100001/fullTextXML"] = (200, "application/xml", xml)
    epmc.json("/rest/PMC100002/fullTextXML", {"errCode": 500}, status=500)
    epmc.routes["/oa/PMCxxxx11/PMC100002.zip"] = (200, "application/zip", _zip("PMC100002"))
    epmc.json("/rest/PMC100003/fullTextXML", {"errCode": 404}, status=404)
    conn = open_store(lib.store_path)
    ids = _cands(lib, conn)
    events = []
    out = acquire.fetch(lib, conn, ids, on_progress=events.append)
    by = {o["cand_id"]: o for o in out}

    a = by[ids[0]]
    assert a["status"] == "fetched" and a["format"] == "jats" and a["error"] is None
    assert Path(a["path"]) == lib.inbox_dir / "doi_10.1_xml.xml" and Path(a["path"]).read_bytes() == xml

    b = by[ids[1]]
    assert b["status"] == "fetched" and b["format"] == "pdf"
    assert Path(b["path"]).name == "doi_10.1_fallthrough.pdf"
    assert Path(b["path"]).read_bytes() == b"%PDF-1.4 the paper itself"  # the paper's own PDF, not the larger supplement

    c = by[ids[2]]
    assert c["status"] == "needs-pdf" and c["path"] is None
    assert "HTTP 404" in c["error"] and "NCBI PMC XML: HTTP 404" in c["error"]
    assert c["links"] == {"doi": "https://doi.org/10.1/nothing", "europepmc": "https://europepmc.org/article/MED/333"}
    d = by[ids[3]]
    assert d["status"] == "needs-pdf" and d["links"]["europepmc"] == "https://europepmc.org/article/MED/444"
    assert not any("PMC" not in p and "444" in p for p in epmc.seen)  # nothing asked for a paper with no PMCID
    # NCBI is asked only for what Europe PMC's XML did not give, and before the PDF is
    assert _ncbi_asked(epmc.seen) == ["100002", "100003"]
    order = [p for p in epmc.seen if "100002" in p]
    assert [p.split("/")[1] for p in order] == ["rest", "ncbi", "oa"]

    rows = {r["cand_id"]: r for r in acquire.candidates(conn)}
    assert rows[ids[0]]["file"] == "doi_10.1_xml.xml" and rows[ids[0]]["status"] == "fetched"
    assert [w["cand_id"] for w in acquire.wanted(conn)] == [ids[2], ids[3]]
    assert {e["status"] for e in events} == {"fetching", "fetched", "needs-pdf"}
    assert not list(lib.inbox_dir.glob("*.part"))

    # fetched and still waiting in the inbox: the same file, and nothing asked again
    asked = len(epmc.seen)
    again = acquire.fetch(lib, conn, ids[:2])
    assert [o["path"] for o in again] == [a["path"], b["path"]] and len(epmc.seen) == asked
    conn.close()


def test_a_direct_pdf_answer_and_a_zip_without_one(epmc, lib):
    epmc.routes["/oa/PMCxxxx1/PMC5.zip"] = (200, "application/pdf", b"%PDF-1.7 plain")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", b"no pdf here")
    epmc.routes["/oa/PMCxxxx1/PMC6.zip"] = (200, "application/zip", buf.getvalue())
    conn = open_store(lib.store_path)
    ids = acquire.record_search(lib, conn, "q", [
        {"pmcid": "PMC5", "title": "open, no xml flag", "is_open_access": True},
        {"pmcid": "PMC6", "title": "a zip with no pdf", "is_open_access": True},
    ])["cand_ids"]
    one, two = acquire.fetch(lib, conn, ids)
    assert one["status"] == "fetched" and one["format"] == "pdf" and Path(one["path"]).name == "pmcid_PMC5.pdf"
    assert two["status"] == "needs-pdf" and "no PDF" in two["error"]
    assert not any("fullTextXML" in p for p in epmc.seen)  # no XML asked of a candidate that has none
    conn.close()


def test_an_author_manuscript_is_fetched_from_ncbi_as_its_article(epmc, lib):
    # Europe PMC's `core` flags for an NIH author manuscript (PMC5653421, checked live
    # 2026-09-30): in PMC, not open access; `fullTextXML` answers 500 for it, NCBI serves it
    hit = acquire.normalise_hit({"id": "28890257", "source": "MED", "pmid": "28890257", "pmcid": "PMC5653421",
                                 "doi": "10.1016/j.actbio.2017.09.006", "title": "A bioinductive collagen suture",
                                 "isOpenAccess": "N", "inEPMC": "Y", "inPMC": "Y", "hasPDF": "N", "authMan": "Y", "nihAuthMan": "Y"})
    assert hit["has_xml"] is True and hit["is_open_access"] is False  # there is XML anyone may fetch, and it is not Europe PMC's
    article = (FIXTURES / "PMC11278924.xml").read_bytes().split(b"?>", 1)[1]
    epmc.routes["/ncbi/efetch.fcgi"] = (200, "text/xml", _articleset(article))
    conn = open_store(lib.store_path)
    ids = acquire.record_search(lib, conn, "q", [hit])["cand_ids"]
    events = []
    (out,) = acquire.fetch(lib, conn, ids, on_progress=events.append)
    assert out["status"] == "fetched" and out["format"] == "jats" and out["source"] == "ncbi" and out["error"] is None
    saved = Path(out["path"]).read_bytes()
    assert Path(out["path"]).name == "doi_10.1016_j.actbio.2017.09.006.xml"
    assert saved == b'<?xml version="1.0" encoding="UTF-8"?>\n' + article  # the article as NCBI sent it, out of its set
    assert not any("fullTextXML" in p for p in epmc.seen)  # not open access: the REST service is not asked
    (asked,) = [p for p in epmc.seen if p.startswith("/ncbi/")]
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(asked).query) == {"db": ["pmc"], "id": ["5653421"], "tool": ["litrag"]}  # the PMCID, nothing else
    assert {"status": "fetched", "source": "ncbi"}.items() <= events[-1].items()
    conn.close()


def test_what_ncbi_will_not_give_out_still_needs_a_pdf(epmc, lib):
    conn = open_store(lib.store_path)
    ids = acquire.record_search(lib, conn, "q", [
        {"pmcid": "PMC9469745", "doi": "10.1089/ten.tea.2021.0203", "title": "a publisher's deposit, not open"},
        {"pmcid": "PMC9469746", "doi": "10.1/front", "title": "front matter only"},
    ])["cand_ids"]
    # PMC holds a publisher's deposit but NCBI will not give it out (checked live, 2026-09-30)
    epmc.routes["/ncbi/efetch.fcgi"] = (400, "text/xml", b'<?xml version="1.0" encoding="UTF-8" ?><eFetchResult><ERROR>unsupported</ERROR></eFetchResult>')
    (one,) = acquire.fetch(lib, conn, ids[:1])
    assert one["status"] == "needs-pdf" and "NCBI PMC XML: HTTP 400" in one["error"] and one["links"]["doi"]
    # an answer with the article's front and no body is not full text
    epmc.routes["/ncbi/efetch.fcgi"] = (200, "text/xml", _articleset(b'<article><front><article-meta><title-group><article-title>t</article-title></title-group></article-meta></front></article>'))
    (two,) = acquire.fetch(lib, conn, ids[1:])
    assert two["status"] == "needs-pdf" and "no full text" in two["error"]
    assert not list(lib.inbox_dir.glob("*.xml"))
    conn.close()


def test_wanted_is_most_cited_first(lib):
    conn = open_store(lib.store_path)
    ids = acquire.record_search(lib, conn, "q", [
        {"pmid": "1", "title": "cited twice", "cited_by": 2},
        {"pmid": "2", "title": "cited forty times", "cited_by": 40},
        {"pmid": "3", "title": "never cited"},
        {"pmid": "4", "title": "also cited twice", "cited_by": 2},
    ])["cand_ids"]
    with conn:
        conn.execute("UPDATE candidates SET status = 'needs-pdf'")
    assert [w["cand_id"] for w in acquire.wanted(conn)] == [ids[1], ids[0], ids[3], ids[2]]  # ties in the order found
    conn.close()


def test_article_from_an_articleset():
    assert acquire._article_from(b"<eFetchResult><ERROR>x</ERROR></eFetchResult>") is None
    assert acquire._article_from(_articleset(b"<article><front/></article>")) is None  # no body
    got = acquire._article_from(_articleset(b'<article article-type="research-article"><front><article-meta/></front><body><p>x</p></body></article>'))
    assert got == b'<?xml version="1.0" encoding="UTF-8"?>\n<article article-type="research-article"><front><article-meta/></front><body><p>x</p></body></article>'


def test_ncbi_is_sent_contact_fields_only_when_the_person_set_them(monkeypatch):
    monkeypatch.delenv("LITRAG_NCBI_EMAIL", raising=False)
    monkeypatch.delenv("LITRAG_NCBI_API_KEY", raising=False)
    assert acquire.ncbi_url("pmc123", "https://x/eutils") == "https://x/eutils/efetch.fcgi?db=pmc&id=123&tool=litrag"
    monkeypatch.setenv("LITRAG_NCBI_EMAIL", "someone@example.org")
    assert "email=someone%40example.org" in acquire.ncbi_url("PMC123", "https://x/eutils")


def test_unreachable_is_failed_not_needs_pdf(lib, monkeypatch):
    monkeypatch.setenv("LITRAG_EPMC_URL", "http://127.0.0.1:9/rest")
    monkeypatch.setenv("LITRAG_EPMC_PDF_URL", "http://127.0.0.1:9/oa")
    monkeypatch.setenv("LITRAG_NCBI_URL", "http://127.0.0.1:9/ncbi")
    conn = open_store(lib.store_path)
    ids = acquire.record_search(lib, conn, "q", [{"pmcid": "PMC7", "doi": "10.1/x", "title": "t", "has_xml": True, "is_open_access": True}])["cand_ids"]
    (out,) = acquire.fetch(lib, conn, ids, timeout=2)
    assert out["status"] == "failed" and out["error"]
    assert acquire.candidates(conn)[0]["status"] == "failed"
    assert acquire.stage(conn, ids)["changed"] == ids  # a failure can be staged and tried again
    conn.close()


def test_stage_dismiss_and_fetch_staged(epmc, lib):
    epmc.routes["/rest/PMC8/fullTextXML"] = (200, "application/xml", b"<?xml version='1.0'?><article><front/></article>")
    conn = open_store(lib.store_path)
    ids = acquire.record_search(lib, conn, "q", [
        {"pmcid": "PMC8", "title": "a", "has_xml": True, "is_open_access": True},
        {"pmid": "9", "title": "b"},
    ])["cand_ids"]
    assert acquire.stage(conn, [ids[0]])["changed"] == [ids[0]]
    assert acquire.dismiss(conn, [ids[1]])["changed"] == [ids[1]]
    assert acquire.fetch(lib, conn, [ids[1]])[0]["status"] == "dismissed"  # set aside, not fetched
    (out,) = acquire.fetch(lib, conn)  # no ids: every staged one
    assert out["cand_id"] == ids[0] and out["status"] == "fetched"
    assert [r["cand_id"] for r in acquire.candidates(conn, status="dismissed")] == [ids[1]]
    assert acquire.fetch(lib, conn, [999])[0]["error"] == "no such candidate"
    conn.close()


def test_cli_search(epmc, lib, capsys):
    epmc.json("/rest/search", CORE)
    assert acquire.main(["--lib", str(lib.dir), "--search", "hydrogel cartilage", "--size", "3"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["added"] == 3 and out["next_cursor"] == CORE["nextCursorMark"]
    assert acquire.main(["--lib", str(lib.dir), "--list"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 3


def test_pdf_url_blocks_of_ten_thousand():
    assert acquire.pdf_url("PMC11457099", "https://x/OA") == "https://x/OA/PMCxxxx1146/PMC11457099.zip"
    assert acquire.pdf_url("pmc10000", "https://x/OA") == "https://x/OA/PMCxxxx1/PMC10000.zip"
