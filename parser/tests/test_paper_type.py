"""What kind of paper this is: every source's label through one canonical table, the most
trusted specific label deciding, a default bucket never making a research paper on its own,
every disagreement a note, and the audit expecting what the type promises."""

import os

from litrag_parser import lanes
from litrag_parser.audit import audit_tree
from litrag_parser.paper_type import Evidence, canonical_label, decide, evidence_of, from_jats, from_record, from_title, shape_of
from litrag_parser.store import open_store
from litrag_parser.tree import build_tree

from test_structure import _doc

JATS_REVIEW = b'<?xml version="1.0"?><article xmlns:xlink="http://www.w3.org/1999/xlink" article-type="review-article" dtd-version="1.2"><front/></article>'
JATS_DEFAULT = b'<article article-type="research-article"><front><article-meta><article-categories><subj-group subj-group-type="heading"><subject>Article</subject></subj-group><subj-group><subject>Chemistry</subject></subj-group></article-categories></article-meta></front></article>'
JATS_SUBJECT = b'<article article-type="research-article"><front><article-meta><article-categories><subj-group subj-group-type="heading"><subject>Study Protocol</subject></subj-group></article-categories></article-meta></front></article>'


def _research(title="A crosslinked collagen scaffold for tendon repair"):
    return build_tree(_doc([
        ("title", title, 1),
        ("section_header", "Abstract", 1), ("text", "We report a crosslinked scaffold and measure its stiffness. The modulus rose with crosslinker content. Cells grew on it.", 1),
        ("section_header", "1 Introduction", 1), ("text", "Tendon injuries are common and heal slowly, which is why scaffolds have been studied for decades in this field.", 1),
        ("section_header", "2 Materials and methods", 1), ("text", "Collagen was cast and crosslinked with genipin for 24 h at room temperature before testing on the Instron.", 1),
        ("section_header", "3 Results", 2), ("text", "The compressive modulus rose from 12 to 48 kPa as the crosslinker concentration increased, as Figure 3 shows.", 2),
        ("section_header", "4 Discussion", 2), ("text", "Our findings demonstrate that the crosslinked scaffolds support cell growth while providing mechanical properties.", 2),
    ]), "k")


def _review(printed=True):
    return build_tree(_doc(([("title", "REVIEW", 1)] if printed else []) + [
        ("title", "Cellulose-based hydrogels for tissue engineering: a sustainable approach", 1),
        ("section_header", "Abstract", 1), ("text", "This review surveys the sources of cellulose, its derivatives and the hydrogels made from them, and their applications in tissue engineering.", 1),
        ("section_header", "1. Introduction", 1), ("text", "Cellulose is the most abundant polymer on earth and has been studied for decades in this field of work.", 1),
        ("section_header", "2. Cellulose Sources and Derivatives", 1), ("text", "Cellulose comes from plants, bacteria and tunicates, and each source gives a different fibril.", 1),
        ("section_header", "3. Synthesis of Cellulose-Based Hydrogels", 2), ("text", "Physical and chemical crosslinking both yield hydrogels, with different mechanics and degradation.", 2),
        ("section_header", "4. Applications in Cartilage and Bone", 2), ("text", "Cellulose hydrogels have been used for cartilage, bone and skin, with different outcomes in each.", 2),
        ("section_header", "5. Conclusions and Future Perspectives", 2), ("text", "Cellulose hydrogels are promising, and several challenges remain before clinical use.", 2),
    ]), "k")


def test_every_spelling_maps_to_one_canonical_type_and_defaults_name_nothing():
    assert canonical_label("Randomized Controlled Trial", "record") == Evidence("research", "rct", "record", "Randomized Controlled Trial", True)
    assert canonical_label("review-article", "jats").type == "review" and canonical_label("Systematic Review", "subject").subtype == "systematic-review"
    assert canonical_label("Letter to the Editor", "printed").type == "letter" and canonical_label("Case Reports", "record").type == "case-report"
    assert canonical_label("Published Erratum", "record") == Evidence("correction", "erratum", "record", "Published Erratum", True)
    assert canonical_label("Practice Guideline", "record").type == "other" and canonical_label("Practice Guideline", "record").subtype == "guideline"
    for default in ("research-article", "Journal Article", "Article", "PAPER", "Research Paper"):
        e = canonical_label(default, "jats")
        assert e is not None and e.type == "research" and not e.specific
    assert canonical_label("Research Support, N.I.H., Extramural", "record") is None and canonical_label("Chemistry", "subject") is None and canonical_label("academicsubjects/med00200", "subject") is None


def test_the_file_the_record_the_subject_line_and_the_title_each_speak():
    assert [(e.type, e.specific) for e in from_jats(JATS_REVIEW)] == [("review", True)]
    assert [(e.type, e.source, e.specific) for e in from_jats(JATS_DEFAULT)] == [("research", "jats", False), ("research", "subject", False)]  # "Chemistry" is a field, left out
    assert [(e.type, e.source, e.specific) for e in from_jats(JATS_SUBJECT)] == [("research", "jats", False), ("protocol", "subject", True)]
    assert from_jats(b"<article>") == [] and from_jats(None) == []
    assert [(e.type, e.subtype, e.specific) for e in from_record("Journal Article; Randomized Controlled Trial; Research Support, N.I.H., Extramural")] == [("research", None, False), ("research", "rct", True)]
    assert [(e.type, e.subtype) for e in from_record(["review-article", "Review", "Journal Article"])] == [("review", None), ("review", None), ("research", None)]
    assert from_record(None) == [] and from_record("") == []
    assert from_title("Cellulose hydrogels for cartilage repair: a systematic review and meta-analysis") == Evidence("review", "systematic-review", "title", "systematic review", True)
    assert from_title("Bilateral patellar tendon rupture in a healthy adult: a case report").type == "case-report"
    assert from_title("Protocol for a randomised controlled trial of early mobilisation after hip fracture").type == "protocol"
    assert from_title("Erratum: Collagen crosslinking in tendon repair").type == "correction" and from_title("Reply to Smith et al.").type == "letter"
    assert from_title("Early mobilisation after hip fracture: a multicentre randomised controlled trial") == Evidence("research", "rct", "title", "randomised controlled trial", True)
    assert from_title("Peer review in the age of large language models") is None and from_title("A crosslinked collagen scaffold for tendon repair") is None
    assert from_title("Reviewing the evidence for collagen crosslinking") is None
    assert from_title("Response to neoadjuvant chemotherapy in triple-negative breast cancer: a cohort study") is None  # a reply names what it replies to
    assert from_title("Correction of hallux valgus deformity using a minimally invasive osteotomy") is None and from_title("Correction to: Collagen crosslinking in tendon repair").type == "correction"
    assert from_title("Protocol for a systematic review of collagen crosslinking in tendon repair").type == "protocol"
    assert from_title("Scale-up of an mHealth program for self-management in adolescents with type 1 diabetes: protocol for an 11-hospital multicenter randomized controlled trial").type == "protocol"  # the subtitle's word, before the trial rule reads the same title
    assert from_title("Cross-education and mirror therapy after anterior cruciate ligament reconstruction: The CROSSMIRV Trial Protocol").type == "protocol"
    assert from_title("A rapid staining protocol for collagen fibrils in tendon sections") is None  # a method's protocol is not a study's


def test_the_most_trusted_specific_label_decides_and_the_subtype_comes_from_any_that_agrees():
    tree = _research("Early mobilisation after hip fracture: a multicentre randomised controlled trial")
    got = decide(tree, jats_xml=JATS_DEFAULT, pub_types="Journal Article; Multicenter Study; Randomized Controlled Trial")
    assert (got["type"], got["subtype"], got["source"]) == ("research", "rct", "record") and got["notes"] == []  # the most specific subtype, not the first the record lists
    got = decide(tree, jats_xml=JATS_DEFAULT, pub_types="Journal Article")
    assert (got["type"], got["subtype"], got["source"]) == ("research", "rct", "title")  # the record and the file are defaults; the title names the trial
    got = decide(_research(), jats_xml=JATS_REVIEW, pub_types="Journal Article; Review")
    assert (got["type"], got["source"]) == ("review", "record") and got["detail"].startswith("record: Review; jats: review-article")
    assert [n["kind"] for n in got["notes"]] == ["type-disagreement"] and "the shape reads as research" in got["notes"][0]["message"]  # methods and results in a "review"
    got = decide(_research(), jats_xml=JATS_REVIEW, pub_types="Journal Article; Randomized Controlled Trial")
    assert (got["type"], got["subtype"], got["source"]) == ("research", "rct", "record") and "the jats says review" in got["notes"][0]["message"]  # two specific labels disagree: the record's word stands, noted


def test_a_default_bucket_never_makes_a_research_paper_on_its_own():
    research = _research()
    got = decide(research, jats_xml=JATS_DEFAULT, pub_types="Journal Article")
    assert (got["type"], got["source"]) == ("research", "default") and "the shape agrees" in got["detail"]
    review = _review(printed=False)
    got = decide(review, jats_xml=JATS_DEFAULT, pub_types="Journal Article")
    assert (got["type"], got["source"]) == ("review", "shape")  # the shape's review rule was measured precise (76 of 76) and decides; the default bucket is noted
    assert [n["kind"] for n in got["notes"]] == ["type-disagreement"] and "a default bucket" in got["notes"][0]["message"]
    assert decide(review)["type"] == "review" and decide(review)["source"] == "shape"
    letter = build_tree(_doc([("title", "On the question of dose", 1), ("text", "Dear Editor, we read the report with interest and wish to raise a question of dose that the authors did not address in their otherwise careful work.", 1)]), "k")
    got = decide(letter, jats_xml=JATS_DEFAULT)
    assert (got["type"], got["source"]) == ("other", "default") and "does not confirm" in got["detail"]  # the letter rule may not decide yet: three of three measured is too few
    assert decide(research)["source"] == "shape" and decide(research)["type"] == "research"  # no label at all: the shape's rule for research was measured and decides


def test_the_shape_reads_lanes_case_headings_and_letters():
    features, verdict = shape_of(_research())
    assert verdict == "research" and features["methods"] and features["results"] and features["topical"] == 0
    features, verdict = shape_of(_review())
    assert verdict == "review" and not features["methods"] and features["topical"] == 3
    case = build_tree(_doc([
        ("title", "Bilateral patellar tendon rupture in a healthy adult", 1),
        ("section_header", "Introduction", 1), ("text", "Bilateral rupture of the patellar tendon is rare and has been reported in fewer than fifty patients in the literature.", 1),
        ("section_header", "Case presentation", 1), ("text", "A 34-year-old man presented to the emergency department after a fall on the stairs with pain in both knees and an inability to stand.", 1),
        ("section_header", "Discussion", 1), ("text", "Bilateral ruptures are associated with systemic disease, and this patient had none, which makes the case unusual.", 1),
    ]), "k")
    assert shape_of(case)[1] == "case-report"
    letter = build_tree(_doc([
        ("title", "Collagen crosslinking and the question of dose", 1),
        ("text", "Dear Editor, we read with interest the recent report on collagen crosslinking and wish to raise a question of dose that the authors did not address.", 1),
        ("text", "The doses used exceed those in clinical practice by an order of magnitude, and the conclusions should be read in that light.", 1),
    ]), "k")
    assert shape_of(letter)[1] == "letter"
    imrad = build_tree(_doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "1 Introduction", 1), ("text", "Tendon injuries are common and heal slowly, which is why scaffolds have been studied for decades in this field.", 1),
        ("section_header", "2 Materials and methods", 1), ("text", "Collagen was cast and crosslinked with genipin for 24 h at room temperature before testing on the Instron.", 1),
        ("section_header", "2.3 Quality assessment of the scaffolds", 1), ("text", "Each scaffold was inspected for defects before testing and rejected when a tear was found.", 1),
        ("section_header", "3 Results", 2), ("text", "The compressive modulus rose from 12 to 48 kPa as the crosslinker concentration increased, as Figure 3 shows.", 2),
        ("section_header", "3.1 Case study: a large defect", 2), ("text", "One scaffold bridged a defect twice the usual size and held for the full seven days of the test.", 2),
        ("section_header", "4 Discussion", 2), ("text", "Our findings demonstrate that the crosslinked scaffolds support cell growth while providing mechanical properties.", 2),
    ]), "k")
    assert shape_of(imrad)[1] == "research"  # a subsection called a case study, or a quality assessment, does not make a case report or a review
    got = decide(case, jats_xml=JATS_DEFAULT)
    assert (got["type"], got["source"]) == ("case-report", "shape")  # a case heading decides: seven of seven measured


def test_a_research_paper_without_a_results_heading_is_read_by_its_order_or_its_measurements():
    # Nature's order: the main text, the discussion, then the methods — the results under the main text's own headings
    nature = build_tree(_doc([
        ("title", "Highly accurate protein structure prediction with a neural network", 1),
        ("section_header", "Main", 1), ("text", "Proteins are essential to life, and predicting their structure from sequence has been an open problem for fifty years in the field.", 1),
        ("section_header", "Discussion", 1), ("text", "The network reaches atomic accuracy on most of the assessment targets, which opens structural biology to sequence-only studies.", 1),
        ("section_header", "Methods", 2), ("text", "The network was trained on structures released before 2018 with the Adam optimiser for two weeks on sixteen accelerators before evaluation.", 2),
    ]), "k")
    features, verdict = shape_of(nature)
    assert verdict == "research" and features["methods_last"] and not features["results"]
    assert decide(nature, jats_xml=JATS_DEFAULT)["type"] == "research"
    # a review with a methodology section, topical sections and no results: the body cites, it does not measure
    review = build_tree(_doc([
        ("title", "Chitosan-based gels: extraction, gelation mechanisms and biomedical applications", 1),
        ("section_header", "1. Introduction", 1), ("text", "Chitosan is a polysaccharide obtained from chitin and has been studied for decades in this field of work by many groups.", 1),
        ("section_header", "2. Methodology", 1), ("text", "The literature was searched in Scopus and Web of Science for articles published between 2010 and 2024 with the terms chitosan and gel.", 1),
        ("section_header", "3. Sources of Chitosan", 1), ("text", "Chitosan is extracted from crustacean shells, fungi and insects, and each source gives a different degree of deacetylation [12,13].", 1),
        ("section_header", "4. Gelation Mechanisms", 2), ("text", "Physical and chemical crosslinking both yield gels, with different mechanics and degradation as reviewed by several authors [14-20].", 2),
        ("section_header", "5. Conclusions", 2), ("text", "Chitosan gels are promising, and several challenges remain before their clinical use can be considered by the community.", 2),
    ]), "k")
    features, verdict = shape_of(review)
    assert verdict is None and features["methods"] and not features["results"] and features["stats"] == 0.0  # unassignable beats misassigned
    got = decide(review, jats_xml=JATS_DEFAULT)
    assert (got["type"], got["source"]) == ("other", "default")
    # a research paper whose results stand under topical headings: the body reports measurements
    topical = build_tree(_doc([
        ("title", "A ranking neural network for the prediction of calcium binding sites", 1),
        ("section_header", "1. Introduction", 1), ("text", "Calcium binding sites are hard to predict from sequence alone, and many methods have been proposed over the years in this field.", 1),
        ("section_header", "2. Materials and methods", 1), ("text", "The network was trained on 1,200 sites from the PDB with five-fold cross-validation and evaluated on a held-out set of 300 sites.", 1),
        ("section_header", "3. Classification of sequences", 1), ("text", "The network classified 92 % of the sites correctly (n = 300, p < 0.001 against the baseline), a mean precision of 0.91 ± 0.02.", 1),
        ("section_header", "4. Experimental validation", 2), ("text", "Binding measured by fluorescence agreed with the prediction for 27 of 30 peptides, a mean affinity of 12 ± 3 μM across the set.", 2),
        ("section_header", "5. Conclusion", 2), ("text", "The ranking approach predicts binding sites better than the earlier methods, and the code is available for the community to use.", 2),
    ]), "k")
    features, verdict = shape_of(topical)
    assert verdict == "research" and not features["results"] and not features["methods_last"] and features["stats"] >= 0.10


def test_a_data_descriptors_own_headings_name_it():
    brief = build_tree(_doc([
        ("title", "Transcriptome datasets of salicylic acid-treated tomato plant cultures", 1),
        ("section_header", "Abstract", 1), ("text", "This article presents RNA sequencing data from tomato cultures treated with salicylic acid under normal and stress conditions.", 1),
        ("section_header", "1. Value of the Data", 1), ("text", "The data are useful to researchers studying plant stress responses because they cover two conditions and three time points.", 1),
        ("section_header", "2. Data Description", 1), ("text", "The dataset comprises twelve libraries, listed in Table 1 with their read counts and accession numbers.", 1),
        ("section_header", "3. Experimental Design, Materials and Methods", 2), ("text", "Cultures were grown for 14 days and treated with 1 mM salicylic acid before RNA was extracted with the kit.", 2),
        ("section_header", "Limitations", 2), ("text", "Only one cultivar was sampled, and the stress condition was a single concentration of salt.", 2),
    ]), "k")
    assert shape_of(brief)[1] == "data"  # Data in Brief's fixed headings, two of them, beside Scientific Data's
    assert decide(brief, jats_xml=JATS_DEFAULT)["type"] == "data"


def test_the_printed_label_goes_through_the_table_first(fake_oracle):
    review = _review()
    ev = evidence_of(review, oracle=fake_oracle)
    assert [(e.type, e.source, e.label, e.specific) for e in ev] == [("review", "printed", "REVIEW", True)]
    stray = build_tree(_doc([("title", "INTRODUCTION", 1), ("title", "A crosslinked collagen scaffold for tendon repair", 1), ("section_header", "1 Introduction", 1), ("text", "Tendon injuries are common and heal slowly, which is why scaffolds have been studied for decades in this field.", 1), ("section_header", "2 Materials and methods", 1), ("text", "Collagen was cast and crosslinked with genipin for 24 h at room temperature before testing on the Instron.", 1), ("section_header", "3 Results", 2), ("text", "The compressive modulus rose from 12 to 48 kPa as the crosslinker concentration increased, as Figure 3 shows.", 2)]), "k")
    assert evidence_of(stray) == [] and decide(stray)["type"] == "research"  # a heading word above the title is a heading, not the article's type
    got = decide(review, oracle=fake_oracle)
    assert (got["type"], got["source"], got["detail"]) == ("review", "printed", "printed: REVIEW")
    lanes._active = None
    assert decide(review)["type"] == "review"  # the table needs no oracle
    research = _research()
    os.environ["LITRAG_TYPE_PROFILE"] = "on"
    try:
        assert decide(research, oracle=fake_oracle)["source"] in ("shape",)  # the shape decides before the profile is asked
    finally:
        os.environ.pop("LITRAG_TYPE_PROFILE", None)


def test_the_audit_expects_what_the_type_promises():
    tree = build_tree(_doc([("title", "A short note on scaffolds and their many uses", 1), ("section_header", "Introduction", 1), ("text", "Scaffolds have been studied for decades in this field of work and many uses have been found for them.", 1)]), "k")
    assert not tree.has_methods
    kinds = {f.kind: f.severity for f in audit_tree(tree)}
    assert kinds.get("no-methods") == "info"
    assert {f.kind: f.severity for f in audit_tree(tree, "research")}.get("no-methods") == "warn"
    assert "no-methods" not in {f.kind for f in audit_tree(tree, "review")}
    assert "no-methods" not in {f.kind for f in audit_tree(tree, "letter")}
    tree.notes.append({"kind": "type-disagreement", "node_id": tree.root.node_id, "page": None, "message": "the jats says review (review-article); the shape reads as research"})
    assert any(f.kind == "type-disagreement" for f in audit_tree(tree, "review"))


def test_the_type_and_subtype_are_columns_and_an_old_store_grows_them(tmp_path):
    import sqlite3

    old = tmp_path / "store.sqlite"
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE papers (key TEXT PRIMARY KEY, doi TEXT, pmid TEXT, pmcid TEXT, title TEXT NOT NULL, file TEXT, sha256 TEXT, format TEXT, pages INTEGER, status TEXT NOT NULL DEFAULT 'queued', error TEXT, parser TEXT, added_at TEXT NOT NULL, parsed_at TEXT, seconds REAL, has_methods INTEGER)")
    c.execute("INSERT INTO papers(key, title, added_at) VALUES ('p', 't', '2026-01-01T00:00:00Z')")
    c.commit()
    c.close()
    conn = open_store(old)
    from litrag_parser.store import set_record, set_type

    set_record(conn, "p", pub_types=["Journal Article", "Randomized Controlled Trial"])
    set_type(conn, "p", "research", "record", "record: Randomized Controlled Trial", "rct")
    row = conn.execute("SELECT type, subtype, type_source, pub_types FROM papers WHERE key = 'p'").fetchone()
    assert (row["type"], row["subtype"], row["type_source"], row["pub_types"]) == ("research", "rct", "record", "Journal Article; Randomized Controlled Trial")
