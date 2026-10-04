"""The library as a graph of works (graph.py): the citations among the papers held, a citation
round against a canned Europe PMC (what a paper cites, what cites it, each work looked up and
filed as a candidate of the next round), the authors as rows, the `works` view, and the picture
the window draws. Nothing here touches the network."""

import json
import re
import urllib.parse
from types import SimpleNamespace

import pytest

from litrag_parser import acquire, graph
from litrag_parser.library import create_library
from litrag_parser.store import file_paper, open_store, save_refs, set_record
from test_acquire import Canned
from test_worker import by, talk


@pytest.fixture
def epmc(monkeypatch):
    c = Canned()
    monkeypatch.setenv("LITRAG_EPMC_URL", f"{c.url}/rest")
    monkeypatch.setenv("LITRAG_EPMC_PDF_URL", f"{c.url}/oa")
    monkeypatch.setenv("LITRAG_NCBI_URL", f"{c.url}/ncbi")
    monkeypatch.setenv("LITRAG_PMC_CLOUD_URL", f"{c.url}/cloud")
    yield c
    c.close()


@pytest.fixture
def lib(tmp_path):
    return create_library(tmp_path, "Tendon")


def _paper(conn, *, doi=None, pmid=None, pmcid=None, title, year, authors=None, refs=(), at="2026-10-01T00:00:00"):
    key = file_paper(conn, title=title, file="", sha256=title, fmt="pdf", doi=doi, pmid=pmid, pmcid=pmcid, now=at).key
    conn.execute("UPDATE papers SET status = 'parsed', parsed_at = ?, year = ? WHERE key = ?", (at, str(year), key))
    conn.commit()
    if authors:
        set_record(conn, key, authors=authors)
    rows = [SimpleNamespace(ref_no=i, node_id=f"{key}#r{i}", ref_id=None, text=r.get("text") or r.get("title") or "", doi=r.get("doi"), pmid=r.get("pmid"),
                            year=r.get("year"), first_author=r.get("first_author"), title=r.get("title")) for i, r in enumerate(refs, start=1)]
    save_refs(conn, key, rows, [])
    return key


def _core(pmid, title, year, *, doi=None, pmcid=None, authors=(("Lyon", "R", "0000-0001-2345-6789"),), cited=4):
    return {"id": pmid, "source": "MED", "pmid": pmid, "pmcid": pmcid, "doi": doi, "title": title, "pubYear": str(year),
            "firstPublicationDate": f"{year}-03-01", "isOpenAccess": "Y" if pmcid else "N", "inEPMC": "Y" if pmcid else "N", "hasPDF": "N",
            "citedByCount": cited, "authorString": ", ".join(f"{f} {i}" for f, i, _ in authors) + ".",
            "authorList": {"author": [{"fullName": f"{f} {i}", "lastName": f, "initials": i, **({"authorId": {"type": "ORCID", "value": o}} if o else {})} for f, i, o in authors]},
            "journalInfo": {"journal": {"title": "Acta Biomaterialia"}}}


def _q(conn, sql, *args):
    """Rows as tuples, to compare with tuples (the store's rows are `sqlite3.Row`)."""
    return [tuple(r) for r in conn.execute(sql, args)]


def _search(records):
    """Europe PMC's search, answering a lookup's ORed identifiers from `records` as `core` hits."""
    def answer(q):
        query = q["query"][0]
        asked_pmids = set(re.findall(r"EXT_ID:(\d+) AND SRC:MED", query))
        asked_dois = {d.lower() for d in re.findall(r'DOI:"([^"]+)"', query)}
        hits = [r for r in records if r.get("pmid") in asked_pmids or (r.get("doi") or "").lower() in asked_dois]
        return 200, "application/json", json.dumps({"hitCount": len(hits), "resultList": {"result": hits}}).encode()
    return answer


def test_a_name_as_papers_print_it():
    assert graph.split_name("Akkus O") == ("Akkus", "O")
    assert graph.split_name("van Dillen T") == ("van Dillen", "T")
    assert graph.split_name("Ozan Akkus") == ("Akkus", "O")
    assert graph.split_name("Jan van der Berg") == ("van der Berg", "J")
    assert graph.split_name("Akkus, Ozan") == ("Akkus", "O")
    assert graph.split_name("Patrawalla NY") == ("Patrawalla", "NY")
    assert graph.split_name("J.R. Smith") == ("Smith", "JR")
    assert graph.split_name("WHO") == ("WHO", None)  # a consortium: nothing invented
    assert graph.person_key("Müller", "H") == graph.person_key("Muller", "HJ") == "muller h"


def test_the_papers_held_cite_each_other_without_the_network(lib):
    conn = open_store(lib.store_path)
    b = _paper(conn, doi="10.1/b", title="Electrochemically aligned collagen threads", year=2008)
    c = _paper(conn, pmid="555", title="Tendon healing in a rabbit model of rupture", year=2015)
    a = _paper(conn, doi="10.1/a", title="A paper citing both", year=2020, refs=[
        {"doi": "10.1/B", "first_author": "Cheng", "year": "2008"},  # by DOI, in another case
        {"text": "Kishore V. Tendon healing in a rabbit model of rupture. J Orthop Res. 2015;3:1-9.", "year": "2015"},  # by its whole title
        {"text": "An entry naming no paper here.", "year": "2001"},
    ])
    graph.sync(conn)
    assert sorted(_q(conn, "SELECT citing, cited, origin, ref_no FROM cites")) == [(a, b, "refs", 1), (a, c, "refs", 2)]
    assert graph.sync(conn)["linked"] == 0  # twice changes nothing
    g = graph.graph(conn)
    assert {n["id"] for n in g["nodes"]} == {a, b, c}
    assert sorted((e["src"], e["dst"]) for e in g["edges"]) == [(a, b), (a, c)]
    assert {n["id"]: n["cited_here"] for n in g["nodes"]} == {a: 0, b: 1, c: 1}
    # read again, its list is linked again from what it now says
    save_refs(conn, a, [SimpleNamespace(ref_no=1, node_id="n", ref_id=None, text="", doi="10.1/b", pmid=None, year=None, first_author=None, title=None)], [])
    conn.execute("UPDATE papers SET parsed_at = '2026-10-02T00:00:00' WHERE key = ?", (a,))
    conn.commit()
    graph.sync(conn)
    assert _q(conn, "SELECT citing, cited FROM cites") == [(a, b)]
    conn.close()


def test_authors_are_rows_and_works_answer_in_order(lib):
    conn = open_store(lib.store_path)
    jats = [{"name": "Ozan Akkus", "family": "Akkus", "given": "Ozan", "affiliations": [], "corresponding": True},
            {"name": "Mousa Younesi", "family": "Younesi", "given": "Mousa", "affiliations": [], "corresponding": False}]
    k1 = _paper(conn, doi="10.1/late", title="Later", year=2017, authors=jats)
    k2 = _paper(conn, doi="10.1/early", title="Earlier", year=2011, authors=[{"name": "Akkus O", "affiliations": [], "corresponding": False}])  # a record's author string
    graph.ensure_schema(conn)
    acquire.upsert_candidate(conn, acquire.normalise_hit(_core("777", "A candidate by Akkus too", 2019, authors=(("Akkus", "O", None), ("Lin", "S", None)))), query="q", now="t")
    conn.commit()
    graph.sync(conn)
    rows = _q(conn, "SELECT w.year, w.title, w.state FROM authors a JOIN works w USING (work) WHERE a.person = 'akkus o' ORDER BY w.year")
    assert rows == [(2011, "Earlier", "held"), (2017, "Later", "held"), (2019, "A candidate by Akkus too", "candidate")]
    assert _q(conn, "SELECT family, initials, orcid FROM authors WHERE work = ? ORDER BY pos", k1) == [("Akkus", "O", None), ("Younesi", "M", None)]
    (first,) = _q(conn, "SELECT first_author, published, round FROM works WHERE work LIKE 'cand:%'")
    assert first == ("Akkus O", "2019-03-01", 1)
    assert k2 in {w for (w,) in conn.execute("SELECT work FROM works WHERE state = 'held'")}
    conn.close()


def test_a_citation_round_files_what_the_papers_cite_as_the_next_round(epmc, lib):
    conn = open_store(lib.store_path)
    held = _paper(conn, doi="10.1/held", title="A paper the library holds already", year=2012)
    a = _paper(conn, pmid="111", doi="10.1/a", title="The first round's paper", year=2020, refs=[
        {"doi": "10.1/held", "first_author": "Islam", "year": "2012"},
        {"pmid": "222", "first_author": "Lyon", "year": "2019", "title": "Cited by both lists"},
        {"doi": "10.9/unknown", "first_author": "Gautieri", "year": "2011", "title": "A work Europe PMC does not know"},
        {"text": "Kim J. A book chapter with no identifier. 2004.", "year": "2004"},
    ])
    epmc.json("/rest/MED/111/references", {"hitCount": 3, "referenceList": {"reference": [
        {"source": "MED", "id": "222", "title": "Cited by both lists", "pubYear": 2019, "citedOrder": 2, "match": "Y"},
        {"source": "MED", "id": "333", "title": "Only Europe PMC matched it", "authorString": "Onck PR, Koeman T.", "pubYear": 2005, "citedOrder": 5, "match": "Y"},
        {"title": "An entry it could not match", "pubYear": 2004, "citedOrder": 4, "match": "N"},
    ]}})
    epmc.routes["/rest/search"] = _search([_core("222", "Cited by both lists", 2019, pmcid="PMC222"), _core("333", "Only Europe PMC matched it", 2005, authors=(("Onck", "PR", None),))])
    out = graph.harvest(lib, conn)
    assert out["papers"] == 2 and out["added"] == 3 and out["held"] == 1 and out["errors"] == []
    cands = {r["pmid"] or r["doi"]: r for r in map(dict, conn.execute("SELECT * FROM candidates"))}
    assert set(cands) == {"222", "333", "10.9/unknown"}
    assert {c["round"] for c in cands.values()} == {2} and {c["status"] for c in cands.values()} == {"found"}
    assert cands["222"]["pmcid"] == "PMC222" and cands["222"]["has_xml"] == 1  # Europe PMC's record: ready to fetch
    assert cands["10.9/unknown"]["title"] == "A work Europe PMC does not know" and cands["10.9/unknown"]["year"] == "2011"  # as the paper printed it
    c222 = f"cand:{cands['222']['cand_id']}"
    assert sorted(_q(conn, "SELECT origin, ref_no FROM cites WHERE citing = ? AND cited = ?", a, c222)) == [("europepmc", None), ("refs", 2)]
    assert _q(conn, "SELECT COUNT(*) FROM cites WHERE citing = ? AND cited = ?", a, held)[0][0] == 1
    assert out["unidentified"] == 1  # Europe PMC's unmatched entry; the paper's own list stands behind Europe PMC's
    # the works it found, the most cited here first
    top = _q(conn, "SELECT title, round, cited_here FROM works WHERE state = 'candidate' ORDER BY cited_here DESC, year DESC")
    assert top[0] == ("Cited by both lists", 2, 1)
    # twice changes nothing, and asks nothing of Europe PMC
    asked = len(epmc.seen)
    again = graph.harvest(lib, conn)
    assert again["papers"] == 0 and again["added"] == 0 and len(epmc.seen) == asked
    assert _q(conn, "SELECT COUNT(*) FROM candidates")[0][0] == 3
    conn.close()


def test_what_cites_a_paper_and_a_candidate_that_becomes_a_paper(epmc, lib):
    conn = open_store(lib.store_path)
    a = _paper(conn, pmid="111", title="A well-cited paper", year=2015)
    epmc.json("/rest/MED/111/references", {"hitCount": 0, "referenceList": {"reference": []}})
    epmc.json("/rest/MED/111/citations", {"hitCount": 1, "citationList": {"citation": [{"source": "MED", "id": "444", "title": "A newer paper citing it", "pubYear": 2024}]}})
    epmc.routes["/rest/search"] = _search([_core("444", "A newer paper citing it", 2024, doi="10.2/newer", authors=(("Zhai", "Y", "0009-0000-9880-9825"),))])
    out = graph.harvest(lib, conn, citations=True)
    assert out["added"] == 1
    (cid,) = _q(conn, "SELECT cand_id FROM candidates WHERE pmid = '444'")[0]
    assert _q(conn, "SELECT citing, cited, origin FROM cites") == [(f"cand:{cid}", a, "europepmc")]
    assert _q(conn, "SELECT orcid FROM authors WHERE work = ?", f"cand:{cid}")[0][0] == "0009-0000-9880-9825"
    # fetched and read: the candidate is the paper now, and so are its citations
    k = _paper(conn, doi="10.2/newer", pmid="444", title="A newer paper citing it", year=2024)
    acquire.reconcile(conn)
    graph.sync(conn)
    assert _q(conn, "SELECT citing, cited FROM cites") == [(k, a)]
    assert _q(conn, "SELECT state, round FROM works WHERE work = ?", k)[0] == ("held", 2)
    assert _q(conn, "SELECT COUNT(*) FROM authors WHERE work = ?", f"cand:{cid}")[0][0] == 0
    g = graph.graph(conn, candidates="none")
    assert [(e["src"], e["dst"]) for e in g["edges"]] == [(k, a)]
    conn.close()


def test_the_picture_keeps_the_candidates_several_papers_cite(lib):
    conn = open_store(lib.store_path)
    p1 = _paper(conn, doi="10.1/p1", title="One", year=2020)
    p2 = _paper(conn, doi="10.1/p2", title="Two", year=2021)
    graph.ensure_schema(conn)
    now = "t"
    c_both, _ = acquire.upsert_candidate(conn, {"doi": "10.5/both", "title": "Cited by both", "year": "2010"}, query="q", now=now, round=2)
    c_one, _ = acquire.upsert_candidate(conn, {"doi": "10.5/one", "title": "Cited by one", "year": "2011"}, query="q", now=now, round=2)
    conn.executemany("INSERT INTO cites VALUES (?, ?, 'refs', NULL)", [(p1, f"cand:{c_both}"), (p2, f"cand:{c_both}"), (p1, f"cand:{c_one}")])
    conn.commit()
    g = graph.graph(conn, candidates="cited", min_cited=2)
    assert {n["id"] for n in g["nodes"]} == {p1, p2, f"cand:{c_both}"} and g["hidden"] == 1
    both = next(n for n in g["nodes"] if n["state"] == "candidate")
    assert both["label"] == "Cited 2010" and both["cited_here"] == 2 and both["round"] == 2
    assert len(graph.graph(conn, candidates="all")["nodes"]) == 4
    conn.close()


def test_the_worker_answers_sql_over_works_and_the_graph(tmp_path, monkeypatch):
    for env in ("LITRAG_EPMC_URL", "LITRAG_NCBI_URL", "LITRAG_PMC_CLOUD_URL", "LITRAG_EPMC_PDF_URL"):
        monkeypatch.setenv(env, "http://127.0.0.1:9/")  # the round below must reach nothing
    events = talk(tmp_path, [{"id": "1", "op": "init", "name": "Tendon"}])
    assert by(events, "1")[0]["event"] == "library"
    conn = open_store(tmp_path / "tendon" / "store.sqlite")
    b = _paper(conn, doi="10.1/b", title="Cited", year=2008, authors=[{"name": "Akkus O", "affiliations": [], "corresponding": False}])
    a = _paper(conn, doi="10.1/a", title="Citing", year=2020, refs=[{"doi": "10.1/b"}])
    conn.close()
    events = talk(tmp_path, [
        {"id": "s", "op": "sql", "lib": "tendon", "sql": "select w.year, w.title from authors a join works w using (work) where a.family = 'Akkus' order by w.year"},
        {"id": "g", "op": "graph", "lib": "tendon"},
        {"id": "w", "op": "sql", "lib": "tendon", "sql": "with x as (select 1) delete from papers"},
        {"id": "r", "op": "round", "lib": "tendon"},
    ])
    assert by(events, "s")[0]["rows"] == [{"year": 2008, "title": "Cited"}]
    g = by(events, "g")[0]
    assert g["event"] == "graph" and [(e["src"], e["dst"]) for e in g["edges"]] == [(a, b)]
    assert by(events, "w")[0]["event"] == "error"
    assert by(events, "r")[0]["event"] == "queued"
    conn = open_store(tmp_path / "tendon" / "store.sqlite")
    assert _q(conn, "SELECT COUNT(*) FROM papers")[0][0] == 2
    conn.close()


def test_a_merge_carries_a_round_s_candidates_and_citations(tmp_path):
    from litrag_parser import projects

    src = create_library(tmp_path, "Source")
    other = create_library(tmp_path, "Other")
    conn = open_store(other.store_path)
    graph.ensure_schema(conn)
    acquire.upsert_candidate(conn, {"doi": "10.7/first", "title": "Takes the target's first id"}, query="q", now="t")  # so the ids differ
    conn.commit()
    conn.close()
    conn = open_store(src.store_path)
    graph.ensure_schema(conn)
    a = _paper(conn, doi="10.1/a", title="Held in the source", year=2020)
    cid, _ = acquire.upsert_candidate(conn, {"doi": "10.5/cited", "title": "Cited by it", "year": "2010"}, query="cited by a", now="t", round=2)
    conn.execute("INSERT INTO cites VALUES (?, ?, 'europepmc', NULL)", (a, f"cand:{cid}"))
    conn.execute("INSERT INTO harvests VALUES (?, 'references', 't', 1)", (a,))
    conn.commit()
    conn.close()
    projects.merge(tmp_path, ["other", "source"], "Merged")
    conn = open_store(tmp_path / "merged" / "store.sqlite")
    (here,) = _q(conn, "SELECT cand_id, round FROM candidates WHERE doi = '10.5/cited'")
    assert here[1] == 2 and here[0] != cid
    assert _q(conn, "SELECT citing, cited FROM cites") == [(a, f"cand:{here[0]}")]
    assert _q(conn, "SELECT paper, kind FROM harvests WHERE kind = 'references'") == [(a, "references")]
    conn.close()


def test_a_round_that_cannot_reach_europe_pmc_files_nothing_and_tries_again(epmc, lib):
    conn = open_store(lib.store_path)
    a = _paper(conn, pmid="111", title="The first round's paper", year=2020, refs=[{"doi": "10.9/x", "title": "A cited work"}, {"pmid": "111"}])  # the second: itself
    epmc.json("/rest/MED/111/references", {"hitCount": 0, "referenceList": {"reference": []}})
    epmc.json("/rest/search", {"error": "refused"}, status=500)  # the lookup fails
    out = graph.harvest(lib, conn)
    assert out["errors"] and out["added"] == 0
    assert _q(conn, "SELECT COUNT(*) FROM candidates")[0][0] == 0 and _q(conn, "SELECT COUNT(*) FROM harvests WHERE kind = 'references'")[0][0] == 0
    epmc.routes["/rest/search"] = _search([])  # answering again, knowing nothing of the work
    again = graph.harvest(lib, conn)
    assert again["papers"] == 1 and again["added"] == 1  # filed now, from what the paper printed; never itself
    assert _q(conn, "SELECT doi, title FROM candidates") == [("10.9/x", "A cited work")]
    assert _q(conn, "SELECT citing FROM cites") == [(a,)]
    conn.close()
