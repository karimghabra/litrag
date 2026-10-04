"""Methods described elsewhere: "as previously described [1]" followed, in a real store, to the
paper it cites and to that paper's own method — by DOI, PMID or title; by the terms one of its
subsections owns, else its whole methods section; and, when the library lacks the paper, the
entry named, with the candidate that would fetch it. The cue is a closed vocabulary, and a
pointer inside the paper, a supplier's protocol, a figure's credit or a finding is none."""

import json
import time
from pathlib import Path

import pytest

from litrag_parser import acquire, lineage
from litrag_parser.citations import link_citations
from litrag_parser.lineage import described_elsewhere, find_cues, read_paragraph
from litrag_parser.store import file_paper, open_store, save_refs, save_tree, set_record
from litrag_parser.tree import build_tree

FIXTURES = Path(__file__).parent / "fixtures"


def _doc(items):
    """A Docling document from (label, text) pairs; "2.1 …" headings are level 2."""
    texts = []
    for i, (label, text) in enumerate(items):
        level = None
        if label == "section_header":
            first = text.split()[0]
            level = 2 if first[0].isdigit() and "." in first.rstrip(".") else 1
        page, row = 1 + i // 40, i % 40
        texts.append({"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "level": level,
                      "prov": [{"page_no": page, "bbox": {"l": 50, "t": 760 - 18 * row, "r": 500, "b": 750 - 18 * row, "coord_origin": "BOTTOMLEFT"}}]})
    pages = sorted({t["prov"][0]["page_no"] for t in texts})
    return {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in texts]}, "texts": texts, "pictures": [], "tables": [], "groups": [], "pages": {str(p): {"page_no": p, "size": {"width": 600, "height": 850}} for p in pages}}


def _file(conn, items, *, doi=None, pmid=None, year=None, sha, patch_refs=None):
    """Read a paper into the store the way the worker does: file it, save its tree, its entries
    and its citations; `patch_refs` stands in for what a JATS file would have added."""
    key = file_paper(conn, title="x", file=f"{sha}.pdf", sha256=sha, fmt="pdf", doi=doi, pmid=pmid, pmcid=None, now="t").key
    tree = build_tree(_doc(items), key)
    save_tree(conn, key, tree, parser="test", parsed_at="t", seconds=0.0)
    refs, cites = link_citations(tree, None)
    for r in refs:
        for field, value in (patch_refs or {}).get(r.ref_no, {}).items():
            setattr(r, field, value)
    save_refs(conn, key, refs, cites)
    if year:
        set_record(conn, key, year=year)
    conn.commit()
    return key, tree


INTRO = ("section_header", "1 Introduction")
METHODS = ("section_header", "2 Materials and methods")
RESULTS = ("section_header", "3 Results")
REFERENCES = ("section_header", "References")

# B: the electrochemical method itself, cited by DOI
B_TITLE = "An electrochemical fabrication process for the assembly of anisotropically oriented collagen bundles"
B = [
    ("title", B_TITLE),
    INTRO, ("text", "Tendon is made of aligned collagen and no method had made such alignment in vitro at the scale of a bundle."),
    METHODS,
    ("section_header", "2.1 Electrochemical compaction of collagen"),
    ("text", "Monomeric collagen solution was dialyzed against ultrapure water and placed between two parallel electrodes spaced 2 mm apart. A potential of 3 V was applied across the parallel electrodes for 30 s, so that collagen compacted along the isoelectric line between them."),
    ("section_header", "2.2 Polarized light microscopy"),
    ("text", "Bundles were imaged between crossed polarizers on a polarized light microscope, and birefringence was quantified from retardance maps of each bundle."),
    ("section_header", "2.3 Tensile testing"),
    ("text", "Bundles were tested in uniaxial tension at a strain rate of 1 % per second, and the ultimate tensile strength and tangent modulus were read from the stress–strain curves."),
    RESULTS, ("text", "Compacted bundles were birefringent along their length and their strength rose with compaction time over the range examined here."),
]

# C: a cell paper with no genipin in it, cited by PMID
C = [
    ("title", "Bone marrow stromal cell responses on aligned collagen constructs in culture"),
    INTRO, ("text", "Stromal cells respond to the topography they are grown on, which is why aligned substrates have been studied for tendon."),
    METHODS,
    ("section_header", "2.1 Cell isolation"),
    ("text", "Marrow aspirates were collected from donors under consent and plated in expansion medium; non-adherent cells were removed after three days."),
    ("section_header", "2.2 Seeding on constructs"),
    ("text", "Cells at passage three were seeded on each construct at a density of 10,000 per square centimetre and allowed to attach for one hour."),
    ("section_header", "2.3 Immunostaining"),
    ("text", "Constructs were fixed in paraformaldehyde, blocked, and stained for tenomodulin and scleraxis with fluorescent secondary antibodies."),
    RESULTS, ("text", "Cells elongated along the constructs within a day and expressed tenomodulin by the end of the second week of culture."),
]

# D: cited by its title, inside an entry that is only text; the years agree
D_TITLE = "Tenogenic differentiation of human mesenchymal stem cells induced by the topography of aligned collagen threads"
D = [
    ("title", D_TITLE),
    INTRO, ("text", "Topography alone can steer stem cells toward a tendon fate, which this study tests on aligned collagen threads."),
    METHODS,
    ("section_header", "2.1 Genipin crosslinking"),
    ("text", "Threads were immersed in a 0.625 % genipin solution in ethanol for three days and rinsed in water; crosslinking turned the threads blue."),
    ("section_header", "2.2 Cell culture"),
    ("text", "Human mesenchymal stem cells were expanded in growth medium and seeded on the threads in a custom chamber for up to fourteen days."),
    ("section_header", "2.3 Gene expression"),
    ("text", "Messenger RNA was extracted at each time point and the expression of scleraxis and tenomodulin was measured by quantitative PCR."),
    RESULTS, ("text", "Cells on the aligned threads expressed tendon markers sooner than those on random substrates of the same chemistry."),
]

# E: cited by an entry's own title (as JATS gives it); E has no year, so no year can disagree
E_TITLE = "A tendon bioreactor for the cyclic loading of collagen constructs"
E = [
    ("title", E_TITLE),
    INTRO, ("text", "Loading shapes tendon in development, so a bioreactor that loads constructs cyclically is the subject of this work."),
    METHODS,
    ("section_header", "2.1 Bioreactor design"),
    ("text", "The bioreactor held eight constructs in grips driven by a linear actuator under displacement control in a sealed chamber."),
    ("section_header", "2.2 Loading regime"),
    ("text", "Constructs were loaded to 3 % strain at 0.5 Hz for one hour a day, with rest between sessions, for up to three weeks."),
]

# F: the same title as an entry, in another year: two papers, not one
F_TITLE = "Collagen threads for ligament repair in a rabbit model"
F = [("title", F_TITLE), INTRO, ("text", "Ligament repair needs a scaffold that is strong on the day of surgery, and threads of collagen may be one."), METHODS,
     ("section_header", "2.1 Surgery"), ("text", "Rabbits were anaesthetised and the ligament was replaced by a bundle of threads fixed with sutures at each end.")]

# H: a review, cited by DOI, with no methods section to show
H = [("title", "A review of collagen scaffolds for tendon and ligament"), INTRO, ("text", "Collagen scaffolds have been made in many ways, and this review compares them by how closely they match native tissue."),
     ("section_header", "2 Conclusions"), ("text", "Alignment and crosslinking matter most, and the methods that give both deserve the closest attention from the field.")]

A_21 = ("Collagen solution was purchased from Advanced BioMatrix [10]. Collagen threads were compacted between parallel electrodes as previously described [1]. "
        "The threads were crosslinked with genipin as reported [2,3]. Previous studies reported that genipin is cytocompatible [4]. "
        "The crosslinked threads were imaged as shown in Fig. 2 [5].")
A_22 = "Threads were cycled in a tendon bioreactor as described elsewhere [6]. Failure tests followed the procedure of [7–9], with a gauge length of 20 mm."
A_23 = "Cells were seeded as described in detail elsewhere. [1] The medium was changed every two days."
A_24 = "Data are reported as mean ± standard deviation and compared by one-way ANOVA with a post hoc test [4]."
A = [
    ("title", "Electrochemically aligned collagen threads for tendon repair"),
    INTRO, ("text", "Electrochemically aligned collagen threads were first made as previously described [1], and have since been used for tendon."),
    METHODS,
    ("section_header", "2.1 Preparation of ELAC threads"), ("text", A_21),
    ("section_header", "2.2 Mechanical testing"), ("text", A_22),
    ("section_header", "2.3 Cell culture"), ("text", A_23),
    ("section_header", "2.4 Statistical analysis"), ("text", A_24),
    RESULTS, ("text", "The modulus of the threads increased with crosslinking, as previously reported [4], and held after a week in culture."),
    REFERENCES,
    ("list_item", f"1. Cheng X, Gurkan UA, Akkus O. {B_TITLE}. Biomaterials. 2008;29:3278-88. doi:10.1016/J.BIOMATERIALS.2008.04.028"),
    ("list_item", "2. Gurkan UA, Kishore V, Akkus O. Stromal cells on aligned collagen. J Biomed Mater Res A. 2010;94:1070-9."),
    ("list_item", f"3. Kishore V, Bullock W, Akkus O. {D_TITLE}. Biomaterials. 2012;33:2137-44."),
    ("list_item", "4. Sung HW, Huang RN, Huang LL. In vitro evaluation of cytotoxicity of a naturally occurring cross-linking reagent. J Biomater Sci. 1999;10:63-78."),
    ("list_item", "5. Smith A, Jones B. Imaging collagen threads in three dimensions. J Microsc. 2020;12:1-9."),
    ("list_item", "6. Younesi M, Islam A, Akkus O. Tendon bioreactor for cyclic loading. Tissue Eng. 2015;21:100-10."),
    ("list_item", f"7. Doe J, Roe K. {F_TITLE}. J Orthop Res. 2016;34:500-9."),
    ("list_item", "8. Roe R. A review of collagen scaffolds for tendon and ligament. Acta Biomater. 2018;70:1-20. doi:10.1000/review.h"),
    ("list_item", "9. Wanted W. An unpublished electrochemical method for collagen. Biofabrication. 2021;13:1-9. doi:10.5555/WANTED.9"),
    ("list_item", "10. Advanced BioMatrix. PureCol product sheet. 2019."),
]

# G: an author–year paper citing B, D and a paper the library lacks
G = [
    ("title", "A second look at electrochemically aligned collagen threads"),
    INTRO, ("text", "Threads of aligned collagen were introduced over a decade ago (Cheng et al., 2008) and have been refined since then."),
    METHODS,
    ("section_header", "2.1 Thread fabrication"),
    ("text", "Threads were made according to the method of Cheng et al. (2008). They were sterilised as described elsewhere (Kishore et al., 2012; Lyon, 2020)."),
    REFERENCES,
    ("list_item", f"Cheng, X., Gurkan, U. A., & Akkus, O. (2008). {B_TITLE}. Biomaterials, 29, 3278-3288. https://doi.org/10.1016/j.biomaterials.2008.04.028"),
    ("list_item", f"Kishore, V., Bullock, W., & Akkus, O. (2012). {D_TITLE}. Biomaterials, 33, 2137-2144."),
    ("list_item", "Lyon, P. (2020). Sterilising collagen without harm. Biomaterials Science, 8, 100-110."),
]


@pytest.fixture
def lib(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    keys = {}
    keys["B"], _ = _file(conn, B, doi="10.1016/j.biomaterials.2008.04.028", year="2008", sha="b")
    keys["C"], _ = _file(conn, C, pmid="22177622", year="2010", sha="c")
    keys["D"], _ = _file(conn, D, year="2012", sha="d")
    keys["E"], _ = _file(conn, E, sha="e")
    keys["F"], _ = _file(conn, F, year="2019", sha="f")
    keys["H"], _ = _file(conn, H, doi="10.1000/review.h", sha="h")
    keys["A"], tree_a = _file(conn, A, doi="10.1000/citing.a", year="2024", sha="a",
                              patch_refs={2: {"pmid": "22177622"}, 6: {"title": E_TITLE + ".", "year": "2015"}})
    keys["G"], tree_g = _file(conn, G, doi="10.1000/citing.g", year="2025", sha="g")
    acquire.ensure_schema(conn)
    with conn:
        acquire.upsert_candidate(conn, {"doi": "10.5555/wanted.9", "title": "An unpublished electrochemical method for collagen", "year": "2021"}, query="electrochemical collagen", now="t", status="needs-pdf")
    yield conn, keys, {**{n.heading: n for n in tree_a.walk() if n.type == "section"}, **{"G " + (n.heading or ""): n for n in tree_g.walk() if n.type == "section"}}
    conn.close()


# -- the cue ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("sentence, kind, cue", [
    ("Collagen threads were electrocompacted as previously described [14].", "as described", "as previously described"),
    ("Threads were made as described [14].", "as described", "as described"),
    ("Threads were made as described previously [14].", "as described", "as described previously"),
    ("as we have previously described [2]", "as described", "as we have previously described"),
    ("The protocol was described elsewhere [3].", "described previously", "described elsewhere"),
    ("This was described in detail elsewhere [3].", "described previously", "described in detail elsewhere"),
    ("The thread was described before [3].", "described previously", "described before"),
    ("The assay is described in more detail by Smith et al. [4].", "described in detail in", "described in more detail by"),
    ("Cells were isolated as reported previously [5].", "as reported", "as reported previously"),
    ("As previously reported, cells were isolated from marrow [5].", "as reported", "As previously reported"),
    ("Constructs were seeded as detailed in [7].", "as detailed", "as detailed"),
    ("ELAC threads were prepared by following a previously published protocol with minimal modifications [6,9,12].", "previously published method", "previously published protocol"),
    ("Genipin crosslinking was carried out according to the method of Smith et al. (2010).", "according to the method of", "according to the method of"),
    ("Samples were stained following the protocol described in [8].", "following the method of", "following the protocol described"),
    ("We followed the procedure of [8] throughout.", "following the method of", "followed the procedure of"),
    ("The scoring system was adapted from [9].", "adapted from", "adapted from"),
    ("The device was modified from Lyon (2020).", "adapted from", "modified from"),
])
def test_the_vocabulary_names_a_method_described_elsewhere(sentence, kind, cue):
    found = find_cues(sentence)
    assert [(c["kind"], c["cue"]) for c in found] == [(kind, cue)]


@pytest.mark.parametrize("sentence", [
    "The gauge length was 20 mm, as shown in Fig. 2 [8].",
    "Previous studies reported similar moduli [3].",
    "Similar values were reported previously [3].",
    "Threads were stained as described above [3].",
    "Threads were stained as described below.",
    "Threads were stained as described in Section 2.3.",
    "Cells were cultured following the procedure in Section 2.1.",
    "Threads were tested as described for the controls above [3].",
    "Results are given as reported in Table 2.",
    "RNA was extracted according to the manufacturer's protocol [5].",
    "RNA was extracted as described by the manufacturer [5].",
    "qPCR was performed as described in the kit's manual [4].",
    "qPCR was performed as described in the method of Quantitect SYBR Green PCR Kit (Qiagen, Hilden, Germany).",  # PMC3258128's
    "Figure adapted from [5] with permission.",
    "The schematic was adapted from [5].",
    "The samples were described before use [3].",
    "Data are reported as mean ± standard deviation [4].",
])
def test_a_pointer_inside_the_paper_a_supplier_a_credit_or_a_finding_is_no_cue(sentence):
    assert find_cues(sentence) == []


def test_every_cue_in_the_vocabulary_is_documented_and_compiles():
    assert len(lineage.CUES) == len({name for name, _ in lineage.CUES}) == 9
    for name, _ in lineage.CUES:
        assert name.split()[0] in lineage.__doc__ or name in ("previously published method", "described in detail in", "following the method of")


# -- the reference ---------------------------------------------------------------------------------


def _leans(text, cited, refs=None, heading="2.1 Preparation of ELAC threads"):
    return [(s.cue, [(l.marker, l.ref_no) for l in s.leans]) for s in read_paragraph("p", text, heading, cited, refs)]


def test_each_cue_sentence_takes_its_own_markers_and_no_others():
    text = ("Collagen [3] was purchased from Sigma (St. Louis, MO). Threads were electrocompacted as previously described [12,14]. "
            "They were crosslinked with genipin [3]. Cells were seeded as described elsewhere. [15] The medium was changed every "
            "two days as reported [16–18]. Tests followed the procedure of Cheng et al. [12].")
    cited = {3: "[3]", 12: "[12,14]", 14: "[12,14]", 15: "[15]", 16: "[16–18]", 17: "[16–18]", 18: "[16–18]"}
    assert _leans(text, cited) == [
        ("as previously described", [("[12,14]", 12), ("[12,14]", 14)]),  # a list
        ("as described elsewhere", [("[15]", 15)]),  # a marker set after the stop is the sentence's own
        ("as reported", [("[16–18]", 16), ("[16–18]", 17), ("[16–18]", 18)]),  # a range
        ("followed the procedure of", []),  # 12 was leaned on already in this paragraph
    ]


def test_the_markers_after_the_cue_else_the_nearest_before_it():
    assert _leans("Threads [4] were made [12,14] as previously described. Then washed [5].", {4: "[4]", 12: "[12,14]", 14: "[12,14]", 5: "[5]"}) == [
        ("as previously described", [("[12,14]", 12), ("[12,14]", 14)])]
    assert _leans("Collagen [4] was compacted as described [14].", {4: "[4]", 14: "[14]"}) == [("as described", [("[14]", 14)])]


def test_a_marker_the_rows_recorded_elsewhere_in_the_paragraph_is_still_found():
    # citations keep one marker an entry: 14 is recorded as "[12,14]", and the cue sentence prints "[14]"
    assert _leans("Cells [12,14] were cultured. Threads were electrocompacted as described [14].", {12: "[12,14]", 14: "[12,14]"}) == [("as described", [("[14]", 14)])]
    assert _leans("Threads were made as previously described [7]–[9]. Then washed.", {7: "[7]–[9]", 8: "[7]–[9]", 9: "[7]–[9]"}) == [
        ("as previously described", [("[7]–[9]", 7), ("[7]–[9]", 8), ("[7]–[9]", 9)])]


def test_superscripts_parentheses_and_author_year():
    assert _leans("Collagen was dialyzed as described previously.14 The threads were then crosslinked as reported 3,5 and stored.", {14: "14", 3: "3,5", 5: "3,5"}) == [
        ("as described previously", [("14", 14)]), ("as reported", [("3,5", 3), ("3,5", 5)])]
    assert _leans("Collagen was dialyzed as described previously.^14 The threads were crosslinked as reported.^3,^5 And so on.", {14: "14", 3: "3,^5", 5: "3,^5"}) == [
        ("as described previously", [("14", 14)]), ("as reported", [("3,5", 3), ("3,5", 5)])]
    # PMC3258128's own sentence: the parenthetical between the cue and the marker is no pointer inside the paper
    text = "Affinity purification experiments were performed as described previously (Supplementary Figure S1, see Supplementary Methods for details) (24,25). Cells were harvested 48 h after transfection."
    assert _leans(text, {24: "(24,25)", 25: "(24,25)"}) == [("as described previously", [("(24,25)", 24), ("(24,25)", 25)])]
    refs = {1: {"first_author": "Cheng", "year": "2008"}, 2: {"first_author": "Lyon", "year": "2020"}, 3: {"first_author": "Kishore", "year": "2012"}}
    text = "Threads were made according to the method of Cheng et al. (2008). They were sterilised as described elsewhere (Lyon, 2020; Kishore et al., 2012)."
    assert _leans(text, {1: "Cheng et al. (2008)", 2: "Lyon, 2020", 3: "Kishore et al., 2012"}, refs) == [
        ("according to the method of", [("Cheng et al. (2008)", 1)]), ("as described elsewhere", [("Lyon, 2020", 2), ("Kishore et al., 2012", 3)])]


def test_a_cue_without_a_marker_leans_on_nothing():
    assert _leans("Threads were made as previously described. They were washed [4].", {4: "[4]"}) == [("as previously described", [])]
    assert _leans("Threads were made as previously described.", {}) == [("as previously described", [])]


# -- the paper and its method ----------------------------------------------------------------------


def test_follows_each_entry_to_the_paper_and_its_method(lib):
    conn, keys, sec = lib
    out = described_elsewhere(conn, sec["2.1 Preparation of ELAC threads"].node_id)
    assert [(a["ref_no"], a["marker"], a["cue"]) for a in out] == [(1, "[1]", "as previously described"), (2, "[2,3]", "as reported"), (3, "[2,3]", "as reported")]
    one, two, three = out
    # by DOI, case ignored: the entry printed it upper-cased
    assert one["paper"] == {"key": keys["B"], "title": B_TITLE, "year": "2008", "by": "doi"}
    assert one["ref"]["doi"] == "10.1016/J.BIOMATERIALS.2008.04.028" and one["ref"]["year"] == "2008" and one["ref"]["first_author"] == "Cheng"
    assert one["sentence"] == "Collagen threads were compacted between parallel electrodes as previously described [1]."
    assert one["method"]["evidence"] == "terms" and one["method"]["heading"] == "2.1 Electrochemical compaction of collagen"
    assert "parallel electrodes" in one["method"]["detail"] and one["method"]["text"].startswith("Monomeric collagen solution")
    assert one["method"]["ancestry"] == ["2 Materials and methods", "2.1 Electrochemical compaction of collagen"] and one["method"]["page"] == 1
    assert one["node_id"] == sec["2.1 Preparation of ELAC threads"].children[0].node_id
    # by PMID: the cited paper names nothing the sentence does, so its whole methods section
    assert two["paper"]["key"] == keys["C"] and two["paper"]["by"] == "pmid"
    assert two["method"]["evidence"] == "section" and two["method"]["heading"] == "2 Materials and methods"
    assert two["method"]["text"].startswith("Marrow aspirates") and "Constructs were fixed" in two["method"]["text"]
    assert "names no subsection" in two["method"]["detail"]
    # by the title inside an entry that is only text, the years agreeing; "genipin" is the mark of D's 2.1
    assert three["paper"]["key"] == keys["D"] and three["paper"]["by"] == "title" and three["paper"]["year"] == "2012"
    assert three["method"]["evidence"] == "terms" and three["method"]["heading"] == "2.1 Genipin crosslinking" and "genipin" in three["method"]["detail"]
    assert all(a["candidate"] is None for a in out)
    # a paragraph asked on its own answers the same
    assert described_elsewhere(conn, one["node_id"]) == out
    assert len(described_elsewhere(conn, one["node_id"], limit=2)) == 2
    assert set(one) == {"node_id", "sentence", "cue", "marker", "ref_no", "ref", "paper", "method", "candidate"}


def test_resolved_with_a_method_first_then_resolved_then_the_entries_the_library_lacks(lib):
    conn, keys, sec = lib
    out = described_elsewhere(conn, sec["2.2 Mechanical testing"].node_id, limit=10)
    assert [a["ref_no"] for a in out] == [6, 8, 7, 9]
    six, eight, seven, nine = out
    # the entry's own title, equal to a paper's that has no year: no year can disagree
    assert six["paper"]["key"] == keys["E"] and six["paper"]["by"] == "title" and six["method"] is not None
    # a review, found by DOI, with no methods section to show
    assert eight["paper"]["key"] == keys["H"] and eight["paper"]["by"] == "doi" and eight["method"] is None
    # F has the title, in 2019; the entry says 2016: two papers, so not found
    assert seven["paper"] is None and seven["method"] is None and seven["marker"] == "[7–9]"
    assert seven["ref"]["year"] == "2016" and F_TITLE in seven["ref"]["text"]
    # not in the library, but a candidate the window can offer to fetch
    assert nine["paper"] is None and nine["candidate"] == {"cand_id": 1, "status": "needs-pdf"}
    assert [a["ref_no"] for a in described_elsewhere(conn, sec["2.2 Mechanical testing"].node_id)] == [6, 8, 7]


def test_a_marker_after_the_stop_and_a_section_fallback(lib):
    conn, keys, sec = lib
    (a,) = described_elsewhere(conn, sec["2.3 Cell culture"].node_id)
    assert a["sentence"] == "Cells were seeded as described in detail elsewhere. [1]" and a["marker"] == "[1]"
    assert a["paper"]["key"] == keys["B"] and a["method"]["evidence"] == "section" and a["method"]["heading"] == "2 Materials and methods"
    assert a["method"]["text"].startswith("Monomeric collagen solution") and "tangent modulus" in a["method"]["text"]


def test_author_year_entries_are_followed_too(lib):
    conn, keys, sec = lib
    out = described_elsewhere(conn, sec["G 2.1 Thread fabrication"].node_id)
    assert [(a["marker"], a["paper"]["key"] if a["paper"] else None, a["paper"]["by"] if a["paper"] else None) for a in out] == [
        ("Cheng et al. (2008)", keys["B"], "doi"), ("Kishore et al., 2012", keys["D"], "title"), ("Lyon, 2020", None, None)]


def test_a_cited_paper_read_again_is_read_again(lib):
    conn, keys, sec = lib
    node = sec["2.1 Preparation of ELAC threads"].node_id
    assert described_elsewhere(conn, node)[0]["method"]["detail"] == "parallel electrodes, collagen"
    # B is read again, and its compaction no longer speaks of parallel electrodes: what was kept is not used
    again = [(label, text.replace("parallel electrodes", "two plates")) for label, text in B]
    tree = build_tree(_doc(again), keys["B"])
    save_tree(conn, keys["B"], tree, parser="test", parsed_at="t", seconds=0.0)
    first = described_elsewhere(conn, node)[0]
    assert first["paper"]["key"] == keys["B"] and first["method"]["detail"] == "collagen" and "two plates" in first["method"]["text"]


def test_no_cue_no_answer(lib):
    conn, keys, sec = lib
    assert described_elsewhere(conn, sec["2.4 Statistical analysis"].node_id) == []
    assert described_elsewhere(conn, f"{keys['A']}#nothing") == []
    assert described_elsewhere(conn, keys["A"]) == []  # the document node is not a methods node


def test_the_title_match_wants_one_paper_and_years_that_agree(lib):
    conn, keys, _ = lib
    library = lineage._Library(conn)
    entry = {"doi": None, "pmid": None, "year": "2012", "title": None, "text": f"Kishore V. {D_TITLE}. Biomaterials. 2012."}
    assert library.resolve(entry, keys["A"])[1] == "title"
    assert library.resolve({**entry, "year": "2013"}, keys["A"]) == (None, None)
    assert library.resolve({**entry, "year": None}, keys["A"])[1] == "title"
    b = {"doi": "10.9999/another", "pmid": None, "year": "2008", "title": None, "text": f"Cheng X. {B_TITLE}. Biomaterials. 2008."}
    assert library.resolve(b, keys["A"]) == (None, None)  # B's title, but B's DOI is another: two papers
    assert library.resolve({**b, "doi": None}, keys["A"])[1] == "title"
    assert library.resolve({**entry, "text": "Kishore V. Tenogenic differentiation. 2012."}, keys["A"]) == (None, None)  # a few words are not a title
    assert library.resolve({**entry, "text": "x", "title": D_TITLE.upper()}, keys["A"])[1] == "title"
    assert library.resolve(entry, keys["D"]) == (None, None)  # a paper does not cite itself


# -- the survey and the speed ----------------------------------------------------------------------


def test_the_survey_counts_methods_only(lib, capsys):
    conn, keys, _ = lib
    s = lineage.survey(conn, examples=4)
    assert s["papers"] == 8 and s["papers_with_methods"] == 7 and s["methods_paragraphs"] == 17
    assert s["paragraphs_with_cue"] == 4  # A 2.1, 2.2, 2.3 and G 2.1; not A's introduction or results
    assert s["cue_sentences"] == 7 and s["cue_sentences_cited"] == 7 and s["cue_sentences_uncited"] == 0
    assert s["cues"] == {"as described": 4, "as reported": 1, "following the method of": 1, "according to the method of": 1}
    assert s["entries"] == 11 and s["resolved"] == 8 and s["unresolved"] == 3
    assert (s["resolved_doi"], s["resolved_pmid"], s["resolved_title"]) == (4, 1, 3)
    assert (s["method_terms"], s["method_section"], s["method_none"]) == (3, 4, 1)
    assert s["candidates"] == {"needs-pdf": 1}
    assert s["timing"]["calls"] > 20 and len(s["examples"]) == 4


def test_the_command_line_reads_a_library(tmp_path, capsys):
    lib_dir = tmp_path / "lib"
    conn = open_store(lib_dir / "store.sqlite")
    _file(conn, B, doi="10.1016/j.biomaterials.2008.04.028", year="2008", sha="b")
    _file(conn, G, doi="10.1000/citing.g", sha="g")
    conn.close()
    assert lineage.main(["--lib", str(lib_dir)]) == 0
    out = capsys.readouterr().out
    assert "found in the library            1 (by DOI 1" in out and "Cheng et al. (2008)" in out
    assert lineage.main(["--lib", str(lib_dir), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report[str(lib_dir)]["resolved_doi"] == 1


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    """A library of realistic size: the Micromachines paper read 30 times under 30 keys, three of
    them under the DOIs its own ELAC paragraph cites, so every copy's cue finds a paper and a
    method; and the synthetic papers beside them."""
    conn = open_store(tmp_path_factory.mktemp("big") / "store.sqlite")
    doc = json.loads((FIXTURES / "PMC11278924.jats.docling.json").read_text("utf-8"))
    dois = ["10.1016/j.biomaterials.2008.04.028", "10.1002/jbm.a.32783", "10.1016/j.biomaterials.2011.11.066"] + [f"10.9999/copy.{i}" for i in range(27)]
    for i, doi in enumerate(dois):
        key = file_paper(conn, title="x", file=f"{i}.xml", sha256=f"s{i}", fmt="jats", doi=doi, pmid=None, pmcid=None, now="t").key
        tree = build_tree(doc, key)
        save_tree(conn, key, tree, parser="test", parsed_at="t", seconds=0.0)
        refs, cites = link_citations(tree, None)
        save_refs(conn, key, refs, cites)
    for items, sha in ((B, "b"), (C, "c"), (D, "d"), (A, "a")):
        _file(conn, items, sha=sha)
    conn.commit()
    yield conn
    conn.close()


def test_fast_on_a_library_of_realistic_size_and_no_scans(big):
    conn = big
    total = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    assert total > 4000
    nodes = [r[0] for r in conn.execute("SELECT node_id FROM nodes WHERE role = 'methods' AND type IN ('section', 'paragraph', 'list_item')")]
    hit = next(r[0] for r in conn.execute("SELECT node_id FROM nodes WHERE heading = '2.2. Preparation of Electrochemical Aligned Collagen Threads' AND paper = 'doi:10.9999/copy.5'"))
    out = described_elsewhere(conn, hit)
    assert [(a["ref_no"], a["paper"]["by"] if a["paper"] else None) for a in out] == [(6, "doi"), (9, "doi"), (12, "doi")]
    assert all(a["method"] is not None for a in out) and out[0]["cue"] == "previously published protocol"
    # the time: every methods node, from cold (a cited paper's first call reads its methods), then again
    for run in ("cold", "warm"):
        if run == "cold":
            lineage.forget()
        times, answered = [], []
        for nid in nodes:
            t0 = time.perf_counter()
            got = described_elsewhere(conn, nid)
            times.append(time.perf_counter() - t0)
            if got:
                answered.append(times[-1])
        times.sort()
        answered.sort()
        mean = sum(times) / len(times)
        print(f"\n{run}: described_elsewhere on {len(times)} methods nodes of {total}: mean {1000 * mean:.2f} ms, p95 {1000 * times[int(0.95 * len(times))]:.2f} ms, max {1000 * times[-1]:.2f} ms;"
              f" the {len(answered)} with an answer: mean {1000 * sum(answered) / len(answered):.2f} ms, max {1000 * answered[-1]:.2f} ms")
        assert len(answered) >= 90  # every copy's 2.2 subsection, its paragraph and its methods section
        assert mean < 0.01 and times[-1] < 0.15
        if run == "warm":
            assert sum(answered) / len(answered) < 0.005
    # and no statement it runs scans nodes, citations or refs
    lineage.forget()
    seen = []
    conn.set_trace_callback(seen.append)
    try:
        described_elsewhere(conn, hit)
        described_elsewhere(conn, out[0]["node_id"])
    finally:
        conn.set_trace_callback(None)
    plans = []
    for sql in seen:
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            plans.extend(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql))
    assert plans and not [p for p in plans if p.startswith("SCAN") and any(t in p.split() for t in ("nodes", "n", "citations", "refs"))], plans
