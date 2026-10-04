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


def _paper(conn, *, doi=None, pmid=None, pmcid=None, title, year, authors=None, refs=(), at="2026-10-01T00:00:00", fmt="pdf"):
    key = file_paper(conn, title=title, file="", sha256=title, fmt=fmt, doi=doi, pmid=pmid, pmcid=pmcid, now=at).key
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
    a = _paper(conn, pmid="111", doi="10.1/a", title="The first round's paper", year=2020, fmt="jats", refs=[  # its DOIs the publisher's own
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
    conn.executemany("INSERT INTO cites VALUES (?, ?, 'europepmc', NULL)", [(p1, f"cand:{c_both}"), (p2, f"cand:{c_both}"), (p1, f"cand:{c_one}")])
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
    a = _paper(conn, pmid="111", title="The first round's paper", year=2020, fmt="jats", refs=[{"doi": "10.9/x", "title": "A cited work"}, {"pmid": "111"}])  # the second: itself
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


# ---------------------------------------------------------------- OpenAlex beside Europe PMC


def _work(w_id, title, year, *, doi=None, pmid=None, authors=(("Shengmao Lin", "0000-0002-8116-3324"),), refs=(), oa_url=None, source="Micromachines"):
    """An OpenAlex work as its API returns one (shaped after W4400292404, checked 2026-10-04)."""
    ids = {"openalex": f"https://openalex.org/{w_id}"}
    if doi:
        ids["doi"] = f"https://doi.org/{doi}"
    if pmid:
        ids["pmid"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}"
    return {"id": f"https://openalex.org/{w_id}", "doi": f"https://doi.org/{doi}" if doi else None, "ids": ids, "title": title,
            "publication_year": year, "publication_date": f"{year}-06-13", "type": "article", "cited_by_count": 81,
            "authorships": [{"author_position": "first" if i == 0 else "middle", "author": {"id": f"https://openalex.org/A{i}{w_id[1:]}", "display_name": n,
                                                                                               "orcid": f"https://orcid.org/{o}" if o else None}} for i, (n, o) in enumerate(authors)],
            "primary_location": {"source": {"display_name": source}}, "open_access": {"is_oa": bool(oa_url), "oa_url": oa_url},
            "referenced_works": [f"https://openalex.org/{r}" for r in refs]}


class _OpenAlex:
    """OpenAlex on the canned server: works by id, DOI or PMID; lists by `openalex:` and `cites:`
    filters; a search; and, when `spent`, a list or a search answered as a spent budget is."""

    def __init__(self, canned, works, citing=None):
        self.works = {w["id"].rsplit("/", 1)[1]: w for w in works}
        self.citing = citing or {}
        self.spent = False
        self.searched = []
        canned.routes["/openalex/works"] = self.listing
        for w_id, w in self.works.items():
            canned.json(f"/openalex/works/{w_id}", w)
            if w.get("doi"):
                canned.json(f"/openalex/works/doi:{w['doi'].split('doi.org/')[1]}", w)
            if (w.get("ids") or {}).get("pmid"):
                canned.json(f"/openalex/works/pmid:{w['ids']['pmid'].rsplit('/', 1)[1]}", w)
        self.results = []  # what a search answers

    def listing(self, q):
        if self.spent:
            return 429, "application/json", json.dumps({"error": "Rate limit exceeded", "message": "Insufficient budget. This request has no API key"}).encode()
        f = (q.get("filter") or [""])[0]
        if "search" in q:
            self.searched.append((q["search"][0], f))
            out = self.results
        elif f.startswith("openalex:"):
            out = [self.works[w] for w in f.split(":", 1)[1].split("|") if w in self.works]
        elif f.startswith("cites:"):
            out = [self.works[w] for w in self.citing.get(f.split(":", 1)[1], [])]
        else:
            out = []
        return 200, "application/json", json.dumps({"meta": {"count": len(out), "next_cursor": None}, "results": out}).encode()


@pytest.fixture
def openalex_on(epmc, monkeypatch):
    monkeypatch.setenv("LITRAG_OPENALEX", "on")
    monkeypatch.setenv("LITRAG_OPENALEX_URL", f"{epmc.url}/openalex")
    monkeypatch.setattr("litrag_parser.openalex.GAP", 0)
    return epmc


def test_openalex_names_what_europe_pmc_does_not(openalex_on, lib):
    epmc = openalex_on
    conn = open_store(lib.store_path)
    a = _paper(conn, pmid="111", doi="10.1/a", title="Computational and Experimental Characterization of Aligned Collagen", year=2024)
    epmc.json("/rest/MED/111/references", {"hitCount": 1, "referenceList": {"reference": [{"source": "MED", "id": "222", "title": "In PubMed", "pubYear": 2019, "match": "Y"}]}})
    epmc.routes["/rest/search"] = _search([_core("222", "In PubMed", 2019, doi="10.2/pubmed", pmcid="PMC222")])
    oa = _OpenAlex(epmc, [
        _work("W1", "Computational and Experimental Characterization of Aligned Collagen", 2024, doi="10.1/a", pmid="111",
              authors=(("Shengmao Lin", "0000-0002-8116-3324"), ("Vipuil Kishore", "0000-0002-2559-1789")), refs=("W222", "W900", "W901")),
        _work("W222", "In PubMed", 2019, doi="10.2/pubmed", pmid="222"),
        _work("W900", "3D printing-assisted design of scaffold structures", 2015, doi="10.1007/s00170-015-7386-6",
              authors=(("Antreas Kantaros", "0000-0001-7927-1468"),), oa_url="https://repository.example/kantaros.pdf", source="The International Journal of Advanced Manufacturing Technology"),
        _work("W901", "A handbook of collagen, with no DOI", 1998, authors=(("Ramachandran G", None),)),
    ])
    out = graph.harvest(lib, conn)
    assert out["errors"] == [] and out["openalex"]["asked"] == 1 and out["openalex"]["works"] == 3 and not out["openalex"]["spent"]
    cands = {r["pmid"] or r["doi"] or r["openalex"]: r for r in map(dict, conn.execute("SELECT * FROM candidates"))}
    assert set(cands) == {"222", "10.1007/s00170-015-7386-6", "W901"}
    assert cands["222"]["pmcid"] == "PMC222" and cands["222"]["openalex"] == "W222"  # Europe PMC's record, OpenAlex's id beside it
    eng = cands["10.1007/s00170-015-7386-6"]  # a journal PubMed never indexed: OpenAlex's record
    assert eng["title"] == "3D printing-assisted design of scaffold structures" and eng["journal"] == "The International Journal of Advanced Manufacturing Technology"
    assert eng["round"] == 2 and eng["oa_url"] == "https://repository.example/kantaros.pdf" and eng["has_xml"] == 0
    assert acquire.links(eng)["open"] == "https://repository.example/kantaros.pdf"
    assert cands["W901"]["title"] == "A handbook of collagen, with no DOI" and cands["W901"]["doi"] is None
    asked_epmc = [urllib.parse.unquote(p) for p in epmc.seen if p.startswith("/rest/search")]
    assert any("s00170" in p for p in asked_epmc) and not any("W901" in p for p in asked_epmc)  # a DOI is asked of Europe PMC; a work no register names is not
    assert _q(conn, "SELECT orcid FROM authors a JOIN works w USING (work) WHERE w.doi = '10.1007/s00170-015-7386-6'") == [("0000-0001-7927-1468",)]
    # the held paper had no authors of its own: OpenAlex's, filled in
    assert [x["name"] for x in json.loads(_q(conn, "SELECT authors FROM papers WHERE key = ?", a)[0][0])] == ["Shengmao Lin", "Vipuil Kishore"]
    edges = {(e["src"], e["dst"]): e["origin"] for e in graph.graph(conn, candidates="all")["edges"]}
    assert edges[(a, f"cand:{cands['222']['cand_id']}")] == "europepmc+openalex"
    assert edges[(a, f"cand:{eng['cand_id']}")] == "openalex"
    asked = len(epmc.seen)
    assert graph.harvest(lib, conn)["papers"] == 0 and len(epmc.seen) == asked  # twice asks nothing
    conn.close()


def test_a_spent_openalex_budget_fetches_one_by_one_and_skips_what_needs_it(openalex_on, lib):
    epmc = openalex_on
    conn = open_store(lib.store_path)
    a = _paper(conn, doi="10.1/a", title="A paper outside PubMed", year=2024)
    oa = _OpenAlex(epmc, [_work("W1", "A paper outside PubMed", 2024, doi="10.1/a", refs=("W900",)),
                          _work("W900", "3D printing-assisted design of scaffold structures", 2015, doi="10.9/eng")],
                   citing={"W1": ["W900"]})
    oa.spent = True
    epmc.routes["/rest/search"] = _search([])
    out = graph.harvest(lib, conn, citations=True)
    assert out["openalex"]["spent"] and any("budget" in e for e in out["errors"])
    assert _q(conn, "SELECT doi FROM candidates") == [("10.9/eng",)]  # its references, one free lookup at a time
    assert _q(conn, "SELECT kind FROM harvests WHERE paper = ? AND kind LIKE 'openalex%'", a) == [("openalex-references",)]  # what cites it waits for a budget
    oa.spent = False
    again = graph.harvest(lib, conn, citations=True)
    assert again["papers"] == 1 and not again["openalex"]["spent"]
    assert _q(conn, "SELECT origin FROM cites WHERE citing LIKE 'cand:%' AND cited = ?", a) == [("openalex",)]  # W900 cites it too
    conn.close()


def test_an_entry_naming_nothing_is_matched_only_by_its_whole_title(openalex_on, lib):
    epmc = openalex_on
    conn = open_store(lib.store_path)
    pdf = _paper(conn, title="A PDF with no identifier", year=2020, refs=[
        {"text": "Kantaros A, Chatzidai N, Karalekas D. 3D printing-assisted design of scaffold structures. Int J Adv Manuf Technol. 2016;82:559–71.", "year": "2016"},
        {"text": "Smith J. Collagen. Academic Press; 2010.", "year": "2010"},
    ])
    right = _work("W900", "3D printing-assisted design of scaffold structures", 2015, doi="10.1007/s00170-015-7386-6", authors=(("Antreas Kantaros", None),))
    near = _work("W950", "3D printing", 2016, doi="10.9/near", authors=(("Antreas Kantaros", None),))
    oa = _OpenAlex(epmc, [right, near])
    oa.results = [near, right]
    epmc.routes["/rest/search"] = _search([])
    out = graph.harvest(lib, conn)
    assert out["openalex"]["searches"] == 2 and out["openalex"]["matched"] == 1  # "Collagen" is too short a title to stand for one work
    assert oa.searched[0][1] == "publication_year:2015-2017"
    assert _q(conn, "SELECT doi FROM candidates") == [("10.1007/s00170-015-7386-6",)]
    assert sorted(_q(conn, "SELECT citing, origin, ref_no FROM cites")) == [(pdf, "openalex", 1), (pdf, "refs", 1)]  # the entry, linked to what its words found
    assert _q(conn, "SELECT ref_no, how FROM ref_works") == [(1, "openalex-search")]
    assert out["unidentified"] == 1
    conn.close()


def test_a_reference_matches_a_work_only_by_its_whole_title_year_and_first_author():
    from litrag_parser import openalex

    w = _work("W900", "3D printing-assisted design of scaffold structures", 2015, authors=(("Antreas Kantaros", None),))
    entry = {"text": "Kantaros A, et al. 3D printing-assisted design of scaffold structures. Int J Adv Manuf Technol 2016", "year": "2016"}
    assert openalex.matches(entry, w)
    assert not openalex.matches({"text": entry["text"].replace("2016", "2012"), "year": "2012"}, w)  # years too far apart
    # what a PDF does to an entry: accents, a hyphen the line break took, a page range read as the year
    assert openalex.matches({"text": "García-García A, Pigeot S. Engineering of immunoinstructive extracellular matrices. Bioact Mater 2022", "year": "2022"},
                            _work("W2", "Engineering of immunoinstructive extracellular matrices", 2022, authors=(("Andrés García‐García", None),)))
    assert openalex.matches({"text": "Wang X. Effects of an injectable platelet-rich fibrin in comparison to plateletrich plasma. 2018", "year": "2018"},
                            _work("W3", "Effects of an injectable platelet-rich fibrin in comparison to platelet-rich plasma", 2017, authors=(("Xuzhu Wang", None),)))
    assert openalex.matches({"text": "Miron RJ et al. Use of platelet-rich fibrin in regenerative dentistry: a systematic review. Clin Oral Investig 21, 1913-1927 (2017).", "year": "1913"},
                            _work("W4", "Use of platelet-rich fibrin in regenerative dentistry: a systematic review", 2017, authors=(("Richard J. Miron", None),)))
    # an entry that prints no title, by what it prints
    assert openalex.cites_by_place_in_print({"text": "Geissler J, Stevanovic M, Injury 2019, 50, S64."}, "geissler", "2019", "50", "S64")
    assert not openalex.cites_by_place_in_print({"text": "Geissler J, Stevanovic M, Injury 2019, 50, S64."}, "geissler", "2019", "51", "S64")  # another volume
    assert not openalex.cites_by_place_in_print({"text": "Geissler J, Injury 2019, 50, S64."}, "geissler", "2019", "50", None)  # no page known: no match
    assert not openalex.matches({**entry, "text": entry["text"].replace("Kantaros", "Lyon")}, w)  # another first author
    assert not openalex.matches({**entry, "text": "Kantaros A. 3D printing-assisted design. 2016"}, w)  # part of the title is not the title
    assert openalex.ident(w) == "openalex:W900" and openalex.ident(_work("W1", "t", 2020, doi="10.1/X", pmid="5")) == "pmid:5"
    h = openalex.hit(_work("W1", "t", 2020, doi="10.1/X", oa_url="https://x/y.pdf"))
    assert (h["doi"], h["openalex"], h["oa_url"], h["is_open_access"], h["has_xml"]) == ("10.1/x", "W1", "https://x/y.pdf", True, False)


def test_one_work_under_two_dois_is_one_candidate(openalex_on, lib):
    epmc = openalex_on
    conn = open_store(lib.store_path)
    a = _paper(conn, pmid="111", doi="10.1/a", title="A paper citing an old one", year=2017)
    title = "Modulation of the formation of adhesions during the healing of injured tendons"
    epmc.json("/rest/MED/111/references", {"hitCount": 1, "referenceList": {"reference": [{"source": "MED", "id": "11041601", "match": "Y"}]}})
    epmc.routes["/rest/search"] = _search([_core("11041601", title, 2000, doi="10.1302/0301-620x.82b7.9892", authors=(("Tang", "JB", None),))])
    # OpenAlex holds the same paper under the publisher's other DOI, and no PMID
    _OpenAlex(epmc, [_work("W1", "A paper citing an old one", 2017, doi="10.1/a", pmid="111", refs=("W9",)),
                     _work("W9", title, 2000, doi="10.1302/0301-620x.82b7.0821054", authors=(("Jin Bo Tang", None),))])
    graph.harvest(lib, conn)
    assert _q(conn, "SELECT pmid, doi, openalex FROM candidates") == [("11041601", "10.1302/0301-620x.82b7.9892", "W9")]
    assert sorted(_q(conn, "SELECT origin FROM cites WHERE citing = ?", a)) == [("europepmc",), ("openalex",)]
    conn.close()


# ---------------------------------------------------------------- passages that cite a work


def _passage(conn, key, n, text, cites, role="results", heading="3. Results"):
    """A paragraph of a held paper and the entries its markers name (`citations`)."""
    node_id = f"{key}#p{n}"
    conn.execute("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, heading, ancestry, text, page) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (node_id, key, None, n, 2, "paragraph", "text", role, heading, json.dumps([heading]), text, 3))
    conn.executemany("INSERT INTO citations(paper, node_id, ref_no, marker) VALUES (?,?,?,?)", [(key, node_id, r, f"[{r}]") for r in cites])
    conn.commit()
    return node_id


def test_each_entry_is_linked_to_its_work_and_each_passage_with_it(openalex_on, lib):
    epmc = openalex_on
    conn = open_store(lib.store_path)
    held = _paper(conn, doi="10.1/held", title="Electrochemical alignment of collagen into threads", year=2008)
    a = _paper(conn, pmid="111", doi="10.1/a", title="A PDF read into a tree", year=2020, refs=[
        {"doi": "10.1/held", "text": "Cheng X. Electrochemical alignment of collagen into threads. 2008.", "year": "2008", "first_author": "Cheng"},
        {"text": "Lyon R, Kishore V. Cited by both lists, by its place. J Tissue Eng. 2019;5:1-9.", "year": "2019", "first_author": "Lyon"},
        {"text": "Gautieri A, Buehler MJ. Hierarchical structure and nanomechanics of collagen microfibrils. Nano Lett. 2011;11:757.", "year": "2011", "first_author": "Gautieri"},
        {"text": "Onck PR. A place Europe PMC fills with another paper. 2004.", "year": "2004", "first_author": "Onck"},
    ])
    epmc.json("/rest/MED/111/references", {"hitCount": 3, "referenceList": {"reference": [
        {"source": "MED", "id": "222", "title": "Cited by both lists, by its place", "authorString": "Lyon R, Kishore V.", "pubYear": 2019, "citedOrder": 2, "match": "Y"},
        {"source": "MED", "id": "444", "title": "Something else entirely", "authorString": "Smith J.", "pubYear": 2004, "citedOrder": 4, "match": "Y"},  # its place, not its author
    ]}})
    epmc.routes["/rest/search"] = _search([_core("222", "Cited by both lists, by its place", 2019), _core("444", "Something else entirely", 2004)])
    _OpenAlex(epmc, [_work("W1", "A PDF read into a tree", 2020, doi="10.1/a", pmid="111", refs=("W3",)),
                     _work("W3", "Hierarchical structure and nanomechanics of collagen microfibrils", 2011, doi="10.1021/nl103943u", authors=(("Alfonso Gautieri", None),))])
    p1 = _passage(conn, a, 1, "Threads were aligned as before [1], and stiffen as fibrils do [2,3].", [1, 2, 3])
    p2 = _passage(conn, a, 2, "Another lab reports the same [3]; a fourth disagrees [4].", [3, 4])
    graph.harvest(lib, conn)
    links = {r[0]: r[1:] for r in _q(conn, "SELECT ref_no, work, how FROM ref_works WHERE paper = ?", a)}
    c222 = _q(conn, "SELECT cand_id FROM candidates WHERE pmid = '222'")[0][0]
    c_nano = _q(conn, "SELECT cand_id FROM candidates WHERE doi = '10.1021/nl103943u'")[0][0]
    assert links[1] == (held, "doi")  # the held paper, by its DOI before its title
    assert links[2] == (f"cand:{c222}", "europepmc")  # Europe PMC's entry at its place, author and year agreeing
    assert links[3] == (f"cand:{c_nano}", "openalex")  # a work of OpenAlex's list, its whole title in the entry
    assert 4 not in links  # Europe PMC's entry at that place names another first author: unlinked
    # the passages that cite each work
    assert sorted(_q(conn, "SELECT node_id, work FROM passage_cites WHERE work = ?", f"cand:{c_nano}")) == [(p1, f"cand:{c_nano}"), (p2, f"cand:{c_nano}")]
    got = graph.passages_citing(conn, f"cand:{c_nano}")
    assert [(g["node_id"], g["marker"], g["how"]) for g in got] == [(p1, "[3]", "openalex"), (p2, "[3]", "openalex")]
    assert got[0]["ancestry"] == ["3. Results"] and got[0]["paper_title"] == "A PDF read into a tree"
    # the edges carry how many passages cite
    edges = {(e["src"], e["dst"]): e for e in graph.graph(conn, candidates="all")["edges"]}
    assert edges[(a, f"cand:{c_nano}")]["passages"] == 2 and edges[(a, held)]["passages"] == 1
    # a node's citations and a paper's entries lead to the works
    from litrag_parser.store import cites_of, refs_of

    by_ref = {c["ref_no"]: c for c in cites_of(conn, p1)}
    assert by_ref[1]["work_paper"] == held and by_ref[3]["work_cand"] == c_nano and by_ref[3]["work_status"] == "found"
    assert {r["ref_no"]: r["work_title"] for r in refs_of(conn, a)}[2] == "Cited by both lists, by its place"
    # read again: linked again from the kept lists, nothing asked
    asked = len(epmc.seen)
    conn.execute("UPDATE papers SET parsed_at = '2026-10-05T00:00:00' WHERE key = ?", (a,))
    conn.commit()
    graph.sync(conn)
    assert {r[0] for r in _q(conn, "SELECT ref_no FROM ref_works WHERE paper = ?", a)} == {1, 2, 3} and len(epmc.seen) == asked
    conn.close()


def test_a_candidate_read_is_where_its_citing_passages_lead(lib):
    conn = open_store(lib.store_path)
    a = _paper(conn, doi="10.1/a", title="Citing", year=2020, refs=[{"doi": "10.5/cited", "text": "Lyon R. The cited paper. 2010."}])
    graph.ensure_schema(conn)
    cid, _ = acquire.upsert_candidate(conn, {"doi": "10.5/cited", "title": "The cited paper", "year": "2010"}, query="cited by a", now="t", round=2)
    conn.commit()
    node = _passage(conn, a, 1, "As shown before [1].", [1])
    graph.sync(conn)
    assert _q(conn, "SELECT work, how FROM passage_cites") == [(f"cand:{cid}", "doi")]
    assert [n["cand_id"] for n in graph.next_to_read(conn)] == [cid]
    # fetched and read
    k = _paper(conn, doi="10.5/cited", title="The cited paper", year=2010)
    acquire.reconcile(conn)
    graph.sync(conn)
    assert _q(conn, "SELECT node_id, work FROM passage_cites") == [(node, k)]
    assert graph.next_to_read(conn) == []
    conn.close()


def test_what_to_read_next_is_what_the_papers_cite_most(lib):
    conn = open_store(lib.store_path)
    p1, p2 = _paper(conn, doi="10.1/p1", title="One", year=2020), _paper(conn, doi="10.1/p2", title="Two", year=2021)
    graph.ensure_schema(conn)
    ids = {}
    for doi, cited, status in (("10.5/twice", 3, "found"), ("10.5/once-old", 900, "found"), ("10.5/once-new", 5, "failed"), ("10.5/dismissed", 1, "dismissed"), ("10.5/wanting", 1, "needs-pdf")):
        ids[doi], _ = acquire.upsert_candidate(conn, {"doi": doi, "title": doi, "year": "2015", "cited_by": cited}, query="q", now="t", status=status, round=2)
    rows = [(p1, f"cand:{ids['10.5/twice']}"), (p2, f"cand:{ids['10.5/twice']}")]
    rows += [(p1, f"cand:{ids[d]}") for d in ("10.5/once-old", "10.5/once-new", "10.5/dismissed", "10.5/wanting")]
    conn.executemany("INSERT INTO cites VALUES (?, ?, 'europepmc', NULL)", rows)
    conn.commit()
    order = [r["cand_id"] for r in graph.next_to_read(conn)]
    assert order == [ids["10.5/twice"], ids["10.5/once-old"], ids["10.5/once-new"]]  # cited here, then cited anywhere; never what was set aside or waits for a person
    # among works cited as often, one that can be read now first: an open XML, or open with a PMCID —
    # a PMCID alone is no promise (PMC shows papers it may not give out)
    conn.execute("UPDATE candidates SET has_xml = 1 WHERE cand_id = ?", (ids["10.5/once-new"],))
    conn.execute("UPDATE candidates SET pmcid = 'PMC9' WHERE cand_id = ?", (ids["10.5/once-old"],))
    conn.commit()
    assert [r["cand_id"] for r in graph.next_to_read(conn)] == [ids["10.5/twice"], ids["10.5/once-new"], ids["10.5/once-old"]]
    assert [r["cand_id"] for r in graph.next_to_read(conn, min_cited=2)] == [ids["10.5/twice"]]
    assert len(graph.next_to_read(conn, most=1)) == 1
    conn.close()


def test_expand_reads_what_can_be_read_and_marks_the_rest_for_a_person(tmp_path, monkeypatch):
    """Measured at scale (2026-10-04): an expansion that spent its count on the most cited works read
    19 of 100, since the most cited of a grown library are mostly classics nothing open is on record
    for (74 of 74 such fetches failed). The count is spent on what can be read; what is passed over
    on the way is fetched too, which marks it for Collect PDFs."""
    c = Canned()
    try:
        for env, path in (("LITRAG_EPMC_URL", "rest"), ("LITRAG_EPMC_PDF_URL", "oa"), ("LITRAG_NCBI_URL", "ncbi"), ("LITRAG_PMC_CLOUD_URL", "cloud")):
            monkeypatch.setenv(env, f"{c.url}/{path}")
        c.json("/rest/MED/111/references", {"hitCount": 2, "referenceList": {"reference": [{"source": "MED", "id": "222", "citedOrder": 1}, {"source": "MED", "id": "333", "citedOrder": 2}]}})
        c.json("/rest/MED/112/references", {"hitCount": 1, "referenceList": {"reference": [{"source": "MED", "id": "333", "citedOrder": 1}]}})
        # 333: cited by both papers, nothing open on record; 222: cited by one, open XML in Europe PMC
        c.routes["/rest/search"] = _search([_core("222", "Open", 2019, pmcid="PMC222"), _core("333", "A classic, closed", 1995)])
        talk(tmp_path, [{"id": "1", "op": "init", "name": "Tendon"}])
        conn = open_store(tmp_path / "tendon" / "store.sqlite")
        _paper(conn, pmid="111", doi="10.1/a", title="One", year=2020)
        _paper(conn, pmid="112", doi="10.1/b", title="Two", year=2021)
        conn.close()
        from litrag_parser.worker import Worker

        seen = []
        monkeypatch.setattr("litrag_parser.worker.emit", seen.append)
        Worker(tmp_path).do_expand({"id": "x", "op": "expand", "lib": "tendon", "most": 1})
        done = [e for e in seen if e.get("event") == "done" and e.get("op") == "expand"][0]
        conn = open_store(tmp_path / "tendon" / "store.sqlite")
        by_pmid = {pmid: cid for pmid, cid in _q(conn, "SELECT pmid, cand_id FROM candidates")}
        assert [r["cand_id"] for r in done["chosen"]] == [by_pmid["222"]]  # the one that could be read
        assert [r["cand_id"] for r in done["passed"]] == [by_pmid["333"]] and done["for_a_person"] == 1  # the more cited, for a person
        assert done["read"] == [] and done["round"]["added"] == 2  # the open one was refused by the canned services
        assert dict(_q(conn, "SELECT pmid, status FROM candidates")) == {"222": "needs-pdf", "333": "needs-pdf"}
        assert [e for e in seen if e.get("event") == "done" and e.get("op") == "fetch"]
        conn.close()
    finally:
        c.close()


def test_an_expansion_takes_the_readable_and_passes_over_the_rest():
    order = [{"cand_id": 1, "readable": 0}, {"cand_id": 2, "readable": 2}, {"cand_id": 3, "readable": 0}, {"cand_id": 4, "readable": 1}, {"cand_id": 5, "readable": 2}]
    take, passed = graph.expansion(order, 2, set())
    assert [r["cand_id"] for r in take] == [2, 4] and [r["cand_id"] for r in passed] == [1, 3]
    take, passed = graph.expansion(order, 2, {2, 4, 1, 3})  # the next try, further down
    assert [r["cand_id"] for r in take] == [5] and passed == []

def test_a_merge_keeps_what_links_the_entries(tmp_path):
    """The lists a round kept are what lines a PDF's entries up with their works; a merge that left
    them behind would leave those entries unlinked for good, since the round is marked asked."""
    from litrag_parser import projects

    src = create_library(tmp_path, "Source")
    conn = open_store(src.store_path)
    graph.ensure_schema(conn)
    refs = [{"text": "Lyon R, Kishore V. Fibres. J Tissue Eng. 2019;5:1-9.", "year": "2019", "first_author": "Lyon"}]
    a = _paper(conn, doi="10.1/a", title="A PDF read into a tree", year=2020, refs=refs)
    cid, _ = acquire.upsert_candidate(conn, {"pmid": "222", "title": "Fibres", "year": "2019"}, query="cited by a", now="t", round=2)
    conn.execute("INSERT INTO ref_lists (paper, source, ord, ident, title, year, first_author) VALUES (?, 'europepmc', 1, 'pmid:222', 'Fibres', '2019', 'Lyon R, Kishore V')", (a,))
    conn.execute("INSERT INTO harvests VALUES (?, 'references', 't', 1)", (a,))
    conn.commit()
    graph.sync(conn)
    assert _q(conn, "SELECT ref_no, how FROM ref_works") == [(1, "europepmc")]
    conn.close()
    projects.merge(tmp_path, ["source"], "Merged")
    conn = open_store(tmp_path / "merged" / "store.sqlite")
    # the rebuild that follows a merge reads the entries again
    save_refs(conn, a, [SimpleNamespace(ref_no=1, node_id="n", ref_id=None, text=refs[0]["text"], doi=None, pmid=None, year="2019", first_author="Lyon", title=None)], [])
    graph.sync(conn)
    here = _q(conn, "SELECT cand_id FROM candidates WHERE pmid = '222'")[0][0]
    assert _q(conn, "SELECT ref_no, work, how FROM ref_works") == [(1, f"cand:{here}", "europepmc")]
    conn.close()


def test_the_links_of_a_pdf_are_measured_against_its_jats(tmp_path):
    truth_lib, test_lib = create_library(tmp_path, "Truth"), create_library(tmp_path, "Test")
    truth = open_store(truth_lib.store_path)
    _paper(truth, doi="10.1/a", title="The paper", year=2020, refs=[
        {"doi": "10.5/right", "first_author": "Lyon", "year": "2019", "text": "Lyon R. Right. 2019."},
        {"pmid": "777", "first_author": "Onck", "year": "2005", "text": "Onck PR. Never reached. 2005."},
        {"doi": "10.5/third", "first_author": "Smith", "year": "2001", "text": "Smith J. Third. 2001."},
    ])
    test = open_store(test_lib.store_path)
    graph.ensure_schema(test)
    a = _paper(test, doi="10.1/a", title="The paper", year=2020, refs=[
        {"text": "Lyon R, Kishore V. Right. J Tissue Eng. 2019.", "year": "2019", "first_author": "Lyon"},
        {"text": "Gautieri A. Not what Europe PMC put at this place. 2011.", "year": "2011", "first_author": "Gautieri"},
    ])
    for doi, title in (("10.5/right", "Right"), ("10.5/wrong", "Wrong")):
        acquire.upsert_candidate(test, {"doi": doi, "title": title}, query="q", now="t", round=2)
    test.executemany("INSERT INTO ref_lists (paper, source, ord, ident, title, year, first_author) VALUES (?, 'europepmc', ?, ?, ?, ?, ?)", [
        (a, 1, "doi:10.5/right", "Right", "2019", "Lyon R"), (a, 2, "doi:10.5/wrong", "Wrong", "2011", "Gautieri A")])
    test.commit()
    out = graph.measure_links(test, truth, show=5)
    t = out["totals"]
    assert (t["linked"], t["right"], t["right_entry"], t["wrong"]) == (2, 1, 1, 1) and t["precision"] == 0.5
    assert (t["reached"], t["gold"]) == (1, 3) and t["by"]["europepmc"]["wrong"] == 1
    assert out["shown"][0]["verdict"] == "wrong" and out["shown"][0]["title"] == "Wrong"
    truth.close()
    test.close()



def test_a_doi_read_off_a_pdf_is_trusted_only_once_a_source_knows_it(openalex_on, lib):
    """Measured on six papers read both ways: every wrong link came from a DOI the PDF's line breaks
    had mangled ("10.1158/00085472…" for 0008-5472). Such a DOI is filed only when Europe PMC or
    OpenAlex knows it; else the entry is left to the lists, which here put the right work at its place."""
    epmc = openalex_on
    conn = open_store(lib.store_path)
    a = _paper(conn, doi="10.1/a", title="A PDF", year=2020, refs=[
        {"doi": "10.1158/00085472.CAN-09-0099", "first_author": "Lu", "year": "2009", "text": "Lu J, Steeg PS. Breast Cancer Metastasis. Cancer Res. 2009. https://doi.org/10.1158/00085472.CAN-09-0099"},
        {"doi": "10.9/real-but-not-in-pubmed", "first_author": "Kantaros", "year": "2015", "text": "Kantaros A. 3D printing-assisted design. 2015."},
    ])
    # the paper's own record gives its PMID, so Europe PMC's list of its references can be asked
    epmc.routes["/rest/search"] = _search([_core("111", "A PDF", 2020, doi="10.1/a"), _core("19470768", "Breast Cancer Metastasis", 2009, doi="10.1158/0008-5472.can-09-0099", authors=(("Lu", "J", None),))])
    epmc.json("/rest/MED/111/references", {"hitCount": 1, "referenceList": {"reference": [
        {"source": "MED", "id": "19470768", "title": "Breast Cancer Metastasis", "authorString": "Lu J, Steeg PS.", "pubYear": 2009, "citedOrder": 1, "match": "Y"}]}})
    _OpenAlex(epmc, [_work("W5", "3D printing-assisted design", 2015, doi="10.9/real-but-not-in-pubmed", authors=(("Antreas Kantaros", None),))])
    out = graph.harvest(lib, conn, openalex=True)
    assert _q(conn, "SELECT pmid FROM papers WHERE key = ?", a) == [("111",)]
    assert out["unverified"] == 1  # the mangled DOI: no candidate under it
    dois = {d for (d,) in _q(conn, "SELECT doi FROM candidates")}
    assert "10.1158/00085472.can-09-0099" not in dois and {"10.1158/0008-5472.can-09-0099", "10.9/real-but-not-in-pubmed"} <= dois
    links = dict((n, how) for n, _w, how in _q(conn, "SELECT ref_no, work, how FROM ref_works WHERE paper = ?", a))
    assert links == {1: "europepmc", 2: "doi"}  # the first by its place in Europe PMC's list; the second by its DOI, which OpenAlex knows
    conn.close()
