"""Headings canonicalised: the author's heading stays, the catalogue's name stands beside it,
a name fills a lane the vocabulary left silent, built headings take the catalogue's names,
and the name is a column a rebuild replays."""

import sqlite3

from litrag_parser.headings import CANON, agreed, canonical_of, lane_of_canonical, top_level_lane
from litrag_parser.store import open_store, paper_tree, save_tree
from litrag_parser.tree import build_tree

from test_structure import _doc, _vary, ABSTRACT, CITING, METHODS, RESULTS


def test_the_catalogue_names_every_spelling_the_corpus_uses_and_its_families():
    assert canonical_of("2. Experimental") == ("Materials and methods", "table")
    assert canonical_of("MATERIALS & METHODS") == ("Materials and methods", "table")
    assert canonical_of("3 | Results and Discussion") == ("Results and discussion", "table")
    assert canonical_of("Declaration of Competing Interest") == ("Conflicts of interest", "table")
    assert canonical_of("CRediT authorship contribution statement") == ("Author contributions", "table")
    assert canonical_of("Availability of data and materials") == ("Data availability", "table")
    assert canonical_of("Institutional Review Board Statement") == ("Ethics", "table")
    assert canonical_of("5. Conclusions and Future Perspectives") == ("Conclusions", "table")
    assert canonical_of("Conclusions and future challenges") == ("Conclusions", "pattern")  # a family, not a listed spelling
    assert canonical_of("Discussion and clinical relevance") == ("Discussion", "pattern")
    assert canonical_of("2.7 Statistical analysis of the data") == ("Statistical analysis", "pattern")
    assert canonical_of("Cellulose Sources and Derivatives") == (None, "")  # a topical heading names nothing the catalogue has
    assert canonical_of("") == (None, "") and canonical_of(None) == (None, "")
    for name, (lane, spellings) in CANON.items():
        assert lane in ("abstract", "introduction", "methods", "results", "results-discussion", "discussion", "references", "back", "other")
        assert spellings and all(s == s.lower() for s in spellings)
    assert lane_of_canonical("Case presentation") == "results" and lane_of_canonical("Funding") == "back" and lane_of_canonical("Nothing") is None
    assert top_level_lane("Declaration of Competing Interest") == "back" and top_level_lane("Case presentation") == "results" and top_level_lane("Limitations of the present study") == "discussion"
    assert top_level_lane("Reference materials") is None and top_level_lane("Image registration") is None and top_level_lane("Contributions of macrophages to fibrosis") is None  # a family never gives references or back matter
    assert top_level_lane("Materials characterization") is None and top_level_lane("Method for detecting mechanical properties") is None  # the methods family is the whole heading
    assert top_level_lane("2. Data Collection and Outcome Assessment") == "methods" and canonical_of("2. Data Collection and Outcome Assessment") == ("Materials", "pattern")  # data collection is a methods family
    assert top_level_lane("Notation", promote=True) is None and top_level_lane("Data availability statement", promote=True) == "back" and top_level_lane("Results across models", promote=True) is None
    assert agreed("References", "methods") is None and agreed("Conclusions", "abstract") is None and agreed("Statistical analysis", "methods") == "Statistical analysis" and agreed("Keywords", "back") == "Keywords" and agreed(None, "methods") is None


def test_a_section_keeps_its_heading_and_gets_the_catalogues_name_beside_it():
    doc = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "Abstract", 1), ("text", ABSTRACT, 1),
        ("section_header", "1. Introduction", 1), ("text", CITING, 1),
        ("section_header", "Cellulose sources", 1), ("text", "Cellulose comes from plants, bacteria and tunicates, and each source gives a different fibril for the scaffold.", 1),
        ("section_header", "2. Experimental", 1), ("text", _vary(METHODS, 0), 1),
        ("section_header", "2.1 Statistics", 1), ("text", "Data were compared by one-way ANOVA with Tukey's test; p < 0.05 was significant.", 1),
        ("section_header", "3. Results and Discussion", 2), ("text", _vary(RESULTS, 0), 2),
        ("section_header", "4. Concluding Remarks", 2), ("text", "The scaffold is stiff and porous, and cells grow on it, which suggests a route to load-bearing repair.", 2),
        ("section_header", "Declaration of Competing Interest", 2), ("text", "The authors declare no competing interests.", 2),
    ])
    doc["texts"][4]["level"] = 2
    tree = build_tree(doc, "k")
    got = [(n.heading, n.role, n.canonical) for n in tree.walk() if n.type == "section"]
    assert got == [
        ("Abstract", "abstract", "Abstract"), ("1. Introduction", "introduction", "Introduction"), ("Cellulose sources", "introduction", None), ("2. Experimental", "methods", "Materials and methods"),
        ("2.1 Statistics", "methods", "Statistical analysis"), ("3. Results and Discussion", "results-discussion", "Results and discussion"),
        ("4. Concluding Remarks", "discussion", "Conclusions"), ("Declaration of Competing Interest", "back", "Conflicts of interest"),
    ]  # a topical subsection keeps its heading, inherits its lane and has no canonical name
    assert "canonical_meaning" not in tree.repairs  # every name came from the catalogue's spellings and families


def test_the_catalogue_fills_a_lane_the_vocabulary_left_silent_but_never_overrides_it():
    doc = _doc([
        ("title", "Bilateral patellar tendon rupture in a healthy adult", 1),
        ("section_header", "Introduction", 1), ("text", CITING, 1),
        ("section_header", "Case presentation", 1), ("text", "A 34-year-old man presented after a fall with pain in both knees and an inability to stand, and radiographs showed patella alta on both sides.", 1),
        ("section_header", "Discussion", 1), ("text", "Bilateral ruptures are associated with systemic disease, and this patient had none.", 1),
        ("section_header", "Data availability statement", 2), ("text", "The data are available from the corresponding author on reasonable request.", 2),
    ])
    tree = build_tree(doc, "k")
    got = {n.heading: (n.role, n.canonical) for n in tree.walk() if n.type == "section"}
    assert got["Case presentation"] == ("results", "Case presentation")  # the vocabulary had no word; the catalogue's lane fills it
    assert got["Data availability statement"] == ("back", "Data availability")
    assert got["Discussion"] == ("discussion", "Discussion")
    assert tree.has_methods is False


def test_a_numbered_heading_takes_no_back_lane_by_meaning(monkeypatch):
    # a review's "8. Regulatory and Ethical Considerations" lies nearest back matter (0.82, 0.12 clear of discussion),
    # but it carries the body's number, between "7. Challenges …" and "9. Conclusions": unassignable beats misassigned
    from litrag_parser import facets

    monkeypatch.setattr(facets, "by_meaning", lambda clean, alone=False: "back" if clean == "regulatory and ethical considerations" else "other")
    doc = _doc([
        ("title", "Hydrogels for intervertebral disc repair: a review", 1),
        ("section_header", "1. Introduction", 1), ("text", CITING, 1),
        ("section_header", "7. Challenges and Future Directions", 1), ("text", "Scale-up and sterilisation remain open questions for every hydrogel reviewed here.", 1),
        ("section_header", "8. Regulatory and Ethical Considerations", 1), ("text", "Regulatory pathways for combination products differ between the FDA and the EMA, and ethical review of first-in-human trials adds a year.", 2),
        ("section_header", "9. Conclusions", 2), ("text", "Hydrogels are close to the clinic for nucleus replacement.", 2),
        ("section_header", "Author Contributions", 2), ("text", "J.S. wrote the manuscript; M.G. revised it.", 2),
    ])
    tree = build_tree(doc, "k")
    got = {n.heading: (n.role, n.canonical) for n in tree.root.children if n.type == "section"}
    assert got["8. Regulatory and Ethical Considerations"] == ("other", None)  # not back, and so not "Ethics" either
    assert got["Author Contributions"] == ("back", "Author contributions")  # an unnumbered statement keeps its lane
    assert facets.role_of("Regulatory and ethical considerations") == "back"  # the verdict itself stands; the number is what refuses it


def test_a_statement_read_between_the_reference_entries_does_not_cut_the_list():
    # a Frontiers PDF: the layout model reads "Generative AI statement" and "Publisher's note" between the entries
    from test_structure import ENTRY

    doc = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "1 Introduction", 1), ("text", CITING, 1),
        ("section_header", "References", 2),
        *[("list_item", ENTRY.format(n=n), 2) for n in range(1, 5)],
        ("section_header", "Generative AI statement", 2), ("text", "The authors declare that no generative AI was used in the creation of this manuscript.", 2),
        ("section_header", "Publisher's note", 2), ("text", "All claims expressed in this article are solely those of the authors and do not necessarily represent those of their affiliated organizations.", 2),
        *[("list_item", ENTRY.format(n=n), 2) for n in range(5, 9)],
    ])
    tree = build_tree(doc, "k")
    refs = next(n for n in tree.root.children if n.type == "section" and n.role == "references")
    assert sum(1 for n in tree.walk() if n.type == "list_item" and n.role == "references") == 8  # every entry, before and after the statements
    assert [n.heading for n in tree.root.children if n.type == "section"] == ["1 Introduction", "References"]  # the statements nest, the list is one section
    assert {n.heading for n in refs.children if n.type == "section"} == {"Generative AI statement", "Publisher's note"}


def test_built_headings_take_the_catalogues_names(fake_oracle):
    from test_structure import _headingless

    tree = build_tree(_headingless(), "k")
    built = [(n.heading, n.canonical) for n in tree.root.children if n.type == "section" and n.label == "built"]
    assert built == [("Materials and methods", "Materials and methods"), ("Results", "Results"), ("Discussion", "Discussion")]


def test_the_name_is_a_column_and_an_old_store_grows_it(tmp_path):
    doc = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "2. Experimental", 1), ("text", _vary(METHODS, 0), 1),
        ("section_header", "3. Results and Discussion", 2), ("text", _vary(RESULTS, 0), 2),
    ])
    tree = build_tree(doc, "k")
    old = tmp_path / "store.sqlite"
    c = sqlite3.connect(old)  # a store from before the column
    c.executescript("CREATE TABLE papers (key TEXT PRIMARY KEY, doi TEXT, pmid TEXT, pmcid TEXT, title TEXT NOT NULL, file TEXT, sha256 TEXT, format TEXT, pages INTEGER, status TEXT NOT NULL DEFAULT 'queued', error TEXT, parser TEXT, added_at TEXT NOT NULL, parsed_at TEXT, seconds REAL, has_methods INTEGER); CREATE TABLE nodes (node_id TEXT PRIMARY KEY, paper TEXT NOT NULL, parent TEXT, ordinal INTEGER NOT NULL, depth INTEGER NOT NULL, type TEXT NOT NULL, label TEXT NOT NULL, level INTEGER, role TEXT NOT NULL, heading TEXT, ancestry TEXT NOT NULL, text TEXT NOT NULL, page INTEGER, bbox_l REAL, bbox_t REAL, bbox_r REAL, bbox_b REAL, self_ref TEXT, table_json TEXT);")
    c.execute("INSERT INTO papers(key, title, added_at) VALUES ('k', 't', '2026-01-01T00:00:00Z')")
    c.commit()
    c.close()
    conn = open_store(old)
    save_tree(conn, "k", tree, parser="t", parsed_at="2026-01-01T00:00:00Z", seconds=0.1)
    rows = conn.execute("SELECT heading, canonical FROM nodes WHERE type = 'section' ORDER BY ordinal").fetchall()
    assert [(r["heading"], r["canonical"]) for r in rows] == [("2. Experimental", "Materials and methods"), ("3. Results and Discussion", "Results and discussion")]
    back = paper_tree(conn, "k")
    sections = [c for c in back["root"]["children"] if c["type"] == "section"]
    assert [s["canonical"] for s in sections] == ["Materials and methods", "Results and discussion"]
