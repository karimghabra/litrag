"""Two readings of one paper compared: the PDF's tree against the XML's. The fixture paper is
in the repository as both; the synthetic pairs break one thing at a time."""

import json
from pathlib import Path

from litrag_parser.pairs import compare, compare_headings, shingles, summary, words
from litrag_parser.tree import build_tree

from test_structure import _doc

FIXTURES = Path(__file__).parent / "fixtures"

INTRO = "Tendon injuries are common and heal slowly, which is why collagen scaffolds have been studied for decades as a way to bridge a gap that sutures alone cannot close."
METHODS = "Collagen was extracted from bovine tendon, cast into molds and crosslinked with genipin for twenty four hours at room temperature before mechanical testing on the Instron frame."
RESULTS = "The compressive modulus rose steadily with the crosslinker concentration, and the swelling ratio fell by half, as the third figure shows for every group tested here."
RESULTS_2 = "Cells seeded on the crosslinked scaffolds spread within a day and remained viable for two weeks of culture in every group that was tested. Cells on the untreated controls detached within the first three days, and none of them remained attached at the end of the first week."
DISCUSSION = "Our findings suggest that the crosslinker, rather than the collagen concentration, governs stiffness, which agrees with earlier reports on genipin and differs from those on glutaraldehyde."


def _paper(methods=(METHODS,), results=(RESULTS, RESULTS_2), extra=(), headings=("1 Introduction", "2 Materials and methods", "3 Results", "4 Discussion")):
    intro, meth, res, disc = headings
    items = [("title", "A crosslinked collagen scaffold for tendon repair", 1)]
    items += [("section_header", intro, 1), ("text", INTRO, 1)]
    items += [("section_header", meth, 1)] + [("text", t, 1) for t in methods]
    items += [("section_header", res, 2)] + [("text", t, 2) for t in results]
    items += [("section_header", disc, 2), ("text", DISCUSSION, 2)]
    items += list(extra)
    return build_tree(_doc(items), "k")


def test_words_are_letters_only_so_the_two_formats_spell_alike():
    assert words("The ﬁbrils [12] were 3.5 µm wide (p < 0.05).") == ["the", "fibrils", "were", "wide"]
    assert words("naïve Poly(ε-caprolactone)") == ["naive", "poly", "caprolactone"]
    assert shingles(["one", "two", "three", "four", "five"]) == {"one two three four", "two three four five"}
    assert shingles(["cell", "culture"]) == {"cell culture"} and shingles(["alone"]) == frozenset()


def test_the_same_reading_twice_agrees_with_itself():
    r = compare(_paper(), _paper())
    assert (r["recall"], r["faithful"], r["precision"]) == (1.0, 1.0, 1.0)
    assert r["paragraphs"]["intact"] == 1.0 and r["paragraphs"]["missing"] == 0.0
    assert r["headings"]["recall"] == 1.0 and r["headings"]["precision"] == 1.0 and r["headings"]["lane_agree"] == 1.0
    assert r["title_same"] and r["confusion"] == {} and r["junk"] == []


def test_a_dropped_paragraph_costs_recall_and_is_named():
    r = compare(_paper(results=(RESULTS,)), _paper())
    assert r["recall"] < 0.85 and r["faithful"] == r["recall"]  # what was read was read into the right lane
    assert r["paragraphs"]["missing"] > 0 and r["missing_text"][0].startswith("Cells seeded")
    assert r["precision"] == 1.0  # nothing the XML does not hold


def test_text_under_the_wrong_heading_is_read_but_not_faithful():
    # the second results paragraph filed under the methods: all of it is found, none of it in its lane
    r = compare(_paper(methods=(METHODS, RESULTS_2), results=(RESULTS,)), _paper())
    assert r["recall"] == 1.0 and r["faithful"] < 0.85
    assert list(r["confusion"]) == ["results → methods"] and r["landed"]["lane methods"] > 0.1
    assert r["by_lane"]["results"]["faithful"] < 0.6 and r["by_lane"]["methods"]["faithful"] == 1.0


def test_a_paragraph_cut_in_two_is_split_and_prose_the_xml_lacks_is_junk():
    a, b = RESULTS_2.split(". ", 1)  # cut between two sentences: a cut inside one the reader mends itself
    a += "."
    running = ("text", "Journal of Tendon Research volume twelve page four hundred and one downloaded from the publisher's site on the first of September by a subscriber.", 2)
    r = compare(_paper(results=(RESULTS, a, b), extra=(running,)), _paper())
    assert r["paragraphs"]["split"] > 0 and r["paragraphs"]["missing"] == 0.0
    assert r["recall"] > 0.9  # only the shingles across the cut are gone
    assert r["precision"] < 1.0 and r["junk_units"] == 1 and r["junk"][0].startswith("Journal of Tendon Research")


def test_headings_found_missing_spurious_and_at_the_wrong_depth():
    xml = _paper()
    pdf = _paper(headings=("1 Introduction", "2 Materials and methods", "Figure legends and notes", "4 Discussion"))
    h = compare_headings(pdf, xml)
    assert h["recall"] == 0.75 and h["missing"] == ["3 Results"] and h["spurious"] == ["Figure legends and notes"]
    assert compare_headings(_paper(headings=("Introduction", "Materials and Methods:", "Results", "Discussion")), xml)["recall"] == 1.0  # numbering and a colon are not the heading


def test_an_xml_states_its_sections_depth_and_a_pdf_does_not():
    # found by reading reviews from both formats: the XML's topical sections had all become the introduction's
    items = [
        ("title", "Collagen scaffolds for tendon repair: a review of the last decade", 1),
        ("section_header", "Introduction", 1), ("text", INTRO, 1),
        ("section_header", "Tendon biology", 1), ("text", METHODS, 1),
        ("section_header", "Growth factors in tendon healing", 1), ("text", RESULTS, 1),
        ("section_header", "Conclusions", 1), ("text", DISCUSSION, 1),
        ("section_header", "Biography", 1), ("text", "Ada Tester is a professor of biomedical engineering whose laboratory studies collagen scaffolds for tendon repair.", 1),
    ]
    xml = build_tree(_doc(items, pages=()), "k")  # no pages: a JATS file, whose <sec> nesting is its own word
    tops = {n.heading: n.role for n in xml.root.children if n.type == "section"}
    assert tops["Tendon biology"] == "other" and tops["Growth factors in tendon healing"] == "other"  # top-level, and no lane they do not name
    assert "Biography" in tops and tops["Biography"] != "discussion"  # not the conclusions' child
    pdf = build_tree(_doc(items), "k")  # pages: the layout model calls every heading level 1, so depth is inferred as before
    nested = {n.heading for n in pdf.walk() if n.type == "section" and (n.level or 1) > 1}
    assert "Tendon biology" in nested


def test_a_lanes_heading_fused_with_the_subheading_under_it_is_two_headings():
    from litrag_parser.tree import split_fused_heading

    assert split_fused_heading("Results and discussion Contrasting glacier mass balance responses during 2021-22 and 2022-23") == ("Results and discussion", "Contrasting glacier mass balance responses during 2021-22 and 2022-23")
    assert split_fused_heading("Methods Coral core collection") == ("Methods", "Coral core collection")
    for whole in ("Results of the logistic regression analysis", "Conclusion: the 2024 report of the Lancet Countdown", "INTRODUCTION TO THE CONCEPT OF MODIFICATION", "Methods Summary", "Results", "Discussion And outlook"):
        assert split_fused_heading(whole) is None, whole
    fused = build_tree(_doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "Introduction", 1), ("text", INTRO, 1),
        ("section_header", "Methods", 1), ("text", METHODS, 1),
        ("section_header", "Results and discussion Stiffness rises with the crosslinker", 2), ("text", RESULTS, 2), ("text", RESULTS_2, 2),
    ]), "k")
    tops = [(n.heading, n.role) for n in fused.root.children if n.type == "section"]
    assert ("Results and discussion", "results-discussion") in tops and fused.repairs.get("unfused") == 1
    sub = next(n for n in fused.walk() if n.type == "section" and n.heading == "Stiffness rises with the crosslinker")
    assert sub.role == "results-discussion" and (sub.level or 1) == 2  # the results no longer stay under the methods
    assert not any(n.role == "methods" and n.text.startswith("The compressive modulus") for n in fused.walk())


def test_a_block_that_says_its_paragraph_twice_says_it_once():
    from litrag_parser.tree import unrepeat

    whole = RESULTS_2 + " " + RESULTS
    cut = " ".join(whole.split()[:31])  # the first copy, cut short in the middle of a sentence
    assert unrepeat(cut + " " + whole) == whole
    assert unrepeat(whole + " " + cut) == whole  # or the whole paragraph first, then its beginning again
    assert unrepeat(whole) == whole and unrepeat("Cells were seeded. Cells were seeded.") == "Cells were seeded. Cells were seeded."  # too short to be sure
    again = "The modulus rose with the crosslinker concentration in every group. " * 2 + "The modulus rose with the crosslinker concentration in every group, and then it fell."
    assert unrepeat(again) == again  # a short sentence said three times is the author's: only a long first copy, said again in full, goes
    # the fixture's own page 7: the paragraph was there twice, and the XML holds it once
    pdf = build_tree(json.loads((FIXTURES / "PMC11278924.docling.json").read_text("utf-8")), "k")
    assert pdf.repairs.get("unrepeated", 0) >= 1
    assert sum(1 for n in pdf.walk() if n.type == "paragraph" and n.text.count("SEM imaging was performed to determine") > 1) == 0


def test_the_fixture_papers_pdf_reads_like_its_xml():
    key = "doi:10.3390/mi15070851"
    pdf = build_tree(json.loads((FIXTURES / "PMC11278924.docling.json").read_text("utf-8")), key)
    xml = build_tree(json.loads((FIXTURES / "PMC11278924.jats.docling.json").read_text("utf-8")), key)
    r = compare(pdf, xml, (FIXTURES / "PMC11278924.xml").read_bytes())
    assert r["title_same"]
    assert r["recall"] >= 0.97 and r["faithful"] >= 0.95 and r["precision"] >= 0.9
    assert r["paragraphs"]["missing"] == 0.0 and r["confusion"] == {}
    assert r["captions"]["as_caption"] == 1.0 and r["references"]["ratio"] >= 0.9 and r["citations"]["ratio"] >= 0.95
    s = summary([{"key": key, **r}])
    assert s["pairs"] == 1 and s["titles_same"] == 1 and s["mean"]["faithful"] == round(r["faithful"], 3)
