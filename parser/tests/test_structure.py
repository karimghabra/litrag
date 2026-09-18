"""Lanes from content where the headings are silent: a paper without headings gets them
built, in its own order, marked as the reader's; a section whose heading names nothing
takes the lane its paragraphs are clearly of, when that lane has a shape of its own; a
heading that names a lane is never overridden, only noted; and every decision replays
from the store without the embedder."""

import json
from pathlib import Path

from litrag_parser import lanes, meaning
from litrag_parser.audit import audit_tree
from litrag_parser.harness import compare
from litrag_parser.structure import viterbi
from litrag_parser.tree import build_tree

FIXTURES = Path(__file__).parent / "fixtures"

INTRO = "Osteoarthritis is the most common joint disease worldwide and a leading cause of disability in older adults. Despite decades of research no disease-modifying treatment is available and management is limited to symptom control."
METHODS = "Type I collagen was extracted from rat tail tendons as previously described. Tendons were dissolved in 0.02 M acetic acid at 4 °C for 48 h, centrifuged at 10,000 g for 30 min, and the supernatant was lyophilised. Statistical analysis was performed with one-way ANOVA."
RESULTS = "The compressive modulus increased from 12 ± 3 kPa to 48 ± 6 kPa as the crosslinker concentration rose from 0.5 to 2 wt% (Figure 3a, p < 0.01). The swelling ratio decreased correspondingly (Table 1) and cell number was significantly higher on the crosslinked scaffolds."
DISCUSSION = "Our findings demonstrate that the crosslinked scaffolds support cell growth while providing mechanical properties in the range of native cartilage. Several limitations should be acknowledged and further studies in large animal models are warranted."
ENTRY = "[{n}] Smith JA, Lee CD, Brown EF. Collagen crosslinking in tendon repair. J Biomed Mater Res A. 2019;107(4):812-821."
ABSTRACT = "We report a crosslinked collagen scaffold for tendon repair and measure its stiffness, swelling and cell viability over seven days in vitro, finding that a short crosslinker stiffens the network without loss of porosity and that cells grow on it as well as on the control, which suggests a route to load-bearing scaffolds."


def _doc(texts, pages=(1, 2)):
    items = [{"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "level": 1 if label == "section_header" else None, "prov": [{"page_no": page, "bbox": {"l": 50, "t": 700 - 12 * i, "r": 500, "b": 690 - 12 * i, "coord_origin": "BOTTOMLEFT"}}]} for i, (label, text, page) in enumerate(texts)]
    return {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items, "pictures": [], "tables": [], "groups": [], "pages": {str(p): {"page_no": p, "size": {"width": 600, "height": 850}} for p in pages}}


def _vary(text, n):
    return f"{text} (as seen in sample {n} of the series, with the same outcome.)"


def _headingless():
    body = [("text", _vary(METHODS, n), 1) for n in range(3)] + [("text", _vary(RESULTS, n), 1) for n in range(3)] + [("text", _vary(DISCUSSION, n), 2) for n in range(3)] + [("text", ENTRY.format(n=n), 2) for n in range(1, 9)]
    return _doc([("title", "A crosslinked collagen scaffold for tendon repair", 1), ("text", "John A. Smith 1 , Maria García 2 , Wei Zhang 1,*", 1), ("text", ABSTRACT, 1)] + body)


def test_a_paper_without_headings_gets_them_built_in_its_own_order(fake_oracle):
    tree = build_tree(_headingless(), "k")
    tops = [(n.heading, n.role, n.label) for n in tree.root.children if n.type == "section"]
    # the reference list was found by its entries' shape before any heading was built; the rest are built
    assert tops == [("Front matter", "other", "section_header"), ("Abstract", "abstract", "section_header"), ("Materials and methods", "methods", "built"), ("Results", "results", "built"), ("Discussion", "discussion", "built"), ("References", "references", "section_header")]
    assert tree.has_methods and tree.repairs.get("built_headings") == 3 and tree.repairs.get("inferred_references") == 8
    assert [n.role for n in tree.walk() if n.type == "paragraph"] == ["abstract"] + ["methods"] * 3 + ["results"] * 3 + ["discussion"] * 3 + ["references"] * 8
    kinds = {f.kind for f in audit_tree(tree)}
    assert "built-heading" in kinds
    # the same paper, with the embedder gone: the store replays the headings
    fake_oracle._embed = lambda texts: (_ for _ in ()).throw(AssertionError("asked the embedder"))
    again = build_tree(_headingless(), "k")
    assert again.to_dict() == tree.to_dict()


def test_a_paper_with_any_heading_after_its_front_matter_is_left_alone(fake_oracle):
    doc = _headingless()
    doc["texts"].insert(2, {"self_ref": "#/texts/h", "parent": {"$ref": "#/body"}, "children": [], "label": "section_header", "text": "Experimental", "level": 1, "prov": [{"page_no": 1, "bbox": {"l": 50, "t": 690, "r": 500, "b": 680, "coord_origin": "BOTTOMLEFT"}}]})
    doc["body"]["children"].insert(2, {"$ref": "#/texts/h"})
    tree = build_tree(doc, "k")
    assert not any(n.label == "built" for n in tree.walk()) and "built_headings" not in tree.repairs


def test_a_run_the_scorer_is_unsure_of_gets_an_untitled_heading_and_stays_other(fake_oracle, monkeypatch):
    hedged = "Then the matter was considered from several angles by the group over the following weeks without any particular outcome being recorded at that time."
    body = [("text", _vary(METHODS, n), 1) for n in range(3)] + [("text", f"{hedged} Round {n}.", 1) for n in range(3)] + [("text", ENTRY.format(n=n), 2) for n in range(1, 9)]
    doc = _doc([("title", "A crosslinked collagen scaffold for tendon repair", 1), ("text", ABSTRACT, 1)] + body)
    tree = build_tree(doc, "k")
    tops = [(n.heading, n.role) for n in tree.root.children if n.type == "section"]
    assert tops == [("Abstract", "abstract"), ("Materials and methods", "methods"), ("(untitled section)", "other"), ("References", "references")]


def test_a_section_whose_heading_names_nothing_takes_the_lane_its_paragraphs_are_of(fake_oracle):
    doc = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "1 Introduction", 1), ("text", _vary(INTRO, 0), 1), ("text", _vary(INTRO, 1), 1), ("text", _vary(INTRO, 2), 1),
        ("section_header", "2 Scaffold fabrication and testing", 1), ("text", _vary(METHODS, 0), 1), ("text", _vary(METHODS, 1), 1), ("text", _vary(METHODS, 2), 1),
        ("section_header", "3 Applications in orthopaedics", 2), ("text", _vary(DISCUSSION, 0), 2), ("text", _vary(DISCUSSION, 1), 2), ("text", _vary(DISCUSSION, 2), 2),
        ("section_header", "4 Results", 2), ("text", _vary(METHODS, 3), 2), ("text", _vary(METHODS, 4), 2), ("text", _vary(METHODS, 5), 2),
    ])
    tree = build_tree(doc, "k")
    lanes_ = {n.heading: n.role for n in tree.root.children if n.type == "section"}
    assert lanes_["2 Scaffold fabrication and testing"] == "methods"  # a lane with a shape of its own, taken from the paragraphs
    assert lanes_["3 Applications in orthopaedics"] == "other"  # discussion-like prose under a topical heading: noted, not taken
    assert lanes_["4 Results"] == "results"  # the heading names a lane; the paragraphs' disagreement is a note
    assert all(n.role == "methods" for n in tree.walk() if n.type == "paragraph" and "2 Scaffold" in n.ancestry)
    assert tree.repairs.get("laned") == 1 and tree.repairs.get("lane_disagreement") == 1
    assert sorted(n["kind"] for n in tree.notes) == ["lane-disagreement", "lane-suggested"]
    assert {f.kind for f in audit_tree(tree)} >= {"lane-disagreement", "lane-suggested"}
    assert tree.roles["methods"] == 4  # the section and its three paragraphs, recounted
    fake_oracle._embed = lambda texts: (_ for _ in ()).throw(AssertionError("asked the embedder"))
    assert build_tree(doc, "k").to_dict() == tree.to_dict()


def test_the_embedder_going_away_mid_paper_leaves_the_counts_true(fake_oracle, monkeypatch):
    doc = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "2 Scaffold fabrication and testing", 1), ("text", _vary(METHODS, 0), 1), ("text", _vary(METHODS, 1), 1), ("text", _vary(METHODS, 2), 1),
        ("section_header", "3 Applications in orthopaedics", 2), ("text", _vary(DISCUSSION, 0), 2), ("text", _vary(DISCUSSION, 1), 2), ("text", _vary(DISCUSSION, 2), 2),
    ])
    real = fake_oracle._embed
    monkeypatch.setattr(fake_oracle, "_embed", lambda texts: None if any("Our findings" in t and "as seen in sample" in t for t in texts) else real(texts))  # gone by the second section
    tree = build_tree(doc, "k")
    assert tree.roles.get("methods") == 4 and tree.has_methods  # what was laned before the outage is counted
    assert sum(1 for n in tree.walk() if n.role == "methods") == tree.roles["methods"]


def test_a_title_docling_called_a_heading_does_not_count_as_one(fake_oracle):
    doc = _headingless()
    doc["texts"][0]["label"] = "section_header"  # Docling's usual word for the big first-page line
    tree = build_tree(doc, "k")
    assert [n.heading for n in tree.root.children if n.type == "section" and n.label == "built"] == ["Materials and methods", "Results", "Discussion"]
    assert tree.title == "A crosslinked collagen scaffold for tendon repair"


def test_the_position_prior_tips_and_never_decides():
    kind = meaning.Kind("toy", {}, 0.5, 0.08, centroids={"a": [1.0, 0.0], "b": [0.0, 1.0]}, position={"a": [0.9] + [0.1 / 9] * 9, "b": [0.01] + [0.11] * 9}, embedder="x")
    assert kind.prior("a", 0.0) == 0.1  # nine times likelier than uniform, clamped
    assert kind.prior("b", 0.0) == -0.1  # a tenth as likely, clamped
    assert kind.prior("a", 0.95) < 0 and kind.prior("a", None) == 0.0 and kind.prior("zzz", 0.5) == 0.0


def test_centroids_from_another_embedder_fall_silent_and_so_does_a_missing_data_file(tmp_path, monkeypatch):
    kind = meaning.Kind("block", {}, 0.5, 0.08, centroids={"a": [1.0, 0.0]}, embedder="someone-else")
    o = meaning.Oracle(None, model="nomic-embed-text")
    o.register(kind)
    assert o.kinds["block"].silent and o.nearest("block", "anything at all").name == "other" and o.scores("block", ["x"]) is None
    monkeypatch.setattr(meaning, "DATA", tmp_path / "nowhere")
    assert meaning.block_kind().silent


def test_a_paper_whose_title_was_not_found_still_gets_its_headings(fake_oracle):
    doc = _headingless()
    doc["texts"][0]["label"] = "text"
    doc["texts"][0]["text"] = "Collagen scaffold repair"  # three words of plain text: not a title candidate
    tree = build_tree(doc, "k")
    assert [n.heading for n in tree.root.children if n.type == "section" and n.label == "built"] == ["Materials and methods", "Results", "Discussion"]


CITING = "Tendon injuries are among the most common musculoskeletal problems and heal slowly [1,2], which is why scaffolds have been studied for decades in this field of work, with mixed results [3]."


def test_an_introduction_orphaned_before_its_heading_is_given_one():
    # no abstract heading, a long first-page abstract, then the introduction's opening paragraphs before the first known heading
    doc = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("text", "John A. Smith 1 , Maria García 2 , Wei Zhang 1,*", 1),
        ("text", ABSTRACT, 1),
        ("text", CITING, 1),
        ("text", "Collagen is the main constituent of tendon and has been the material of choice for its repair for many years now.", 1),
        ("section_header", "2 Materials and methods", 1), ("text", _vary(METHODS, 0), 1),
        ("section_header", "3 Results", 2), ("text", _vary(RESULTS, 0), 2),
    ])
    tree = build_tree(doc, "k")
    tops = [(n.heading, n.role, n.label, len(n.children)) for n in tree.root.children if n.type == "section"]
    assert tops == [("Front matter", "other", "section_header", 1), ("Abstract", "abstract", "section_header", 1), ("Introduction", "introduction", "built", 2), ("2 Materials and methods", "methods", "section_header", 1), ("3 Results", "results", "section_header", 1)]
    assert tree.repairs.get("built_headings") == 1
    assert [n.role for n in tree.walk() if n.type == "paragraph"] == ["abstract", "introduction", "introduction", "methods", "results"]


def test_an_abstract_section_stops_where_the_paragraphs_start_citing():
    doc = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "Abstract", 1), ("text", ABSTRACT, 1),
        ("text", "Keywords: collagen; tendon; scaffold; crosslinking", 1),
        ("text", CITING, 1),
        ("section_header", "2 Materials and methods", 1), ("text", _vary(METHODS, 0), 1),
    ])
    tree = build_tree(doc, "k")
    tops = [(n.heading, n.role, n.label) for n in tree.root.children if n.type == "section"]
    assert tops == [("Abstract", "abstract", "section_header"), ("Front matter", "other", "section_header"), ("Introduction", "introduction", "built"), ("2 Materials and methods", "methods", "section_header")]
    abstract = next(n for n in tree.root.children if n.role == "abstract")
    assert [c.text[:9] for c in abstract.children] == ["We report"]
    # a structured abstract's second paragraph cites nothing and stays
    doc2 = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "Abstract", 1), ("text", ABSTRACT, 1), ("text", "Conclusions: " + ABSTRACT, 1),
        ("section_header", "1 Introduction", 1), ("text", CITING, 1),
    ])
    tree2 = build_tree(doc2, "k")
    assert len(next(n for n in tree2.root.children if n.role == "abstract").children) == 2 and "built_headings" not in tree2.repairs


def test_front_matter_that_cites_is_not_an_introduction():
    # Frontiers: a "Citation:" line with a year in brackets before the abstract's heading; Advanced Science: a
    # numbered affiliation block ("China. 2 Department …"); Artificial Organs: "Abstract: … et al."; Taylor &
    # Francis: a forty-word "To cite this article: … (2011) …" line. None opens an introduction.
    citation = "Citation: Xu T, Yang Q, Peng Y, Xie C and Yu H (2026) A collagen scaffold for the repair of cartilage in the rabbit knee, with a hydrogel. Front. Immunol. 17:1775735. doi: 10.3389/fimmu.2026.1775735"
    doc = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("text", "Tianyi Xu, Qilei Yang, Yue Peng, Congcong Xie, Huimin Yu", 1),
        ("text", citation, 1),
        ("section_header", "Abstract", 1), ("text", ABSTRACT, 1),
        ("section_header", "1 Introduction", 1), ("text", CITING, 1),
        ("section_header", "2 Materials and methods", 1), ("text", _vary(METHODS, 0), 1),
    ])
    tree = build_tree(doc, "k")
    assert [(n.heading, n.role, n.label) for n in tree.root.children if n.type == "section"] == [("Front matter", "other", "section_header"), ("Abstract", "abstract", "section_header"), ("1 Introduction", "introduction", "section_header"), ("2 Materials and methods", "methods", "section_header")]
    assert "built_headings" not in tree.repairs
    affiliations = "1, Foot & Ankle Surgery, Department of Orthopaedics, Shanghai Sixth People's Hospital Affiliated to Shanghai Jiao Tong University School of Medicine, Shanghai, 200233 China. 2 Department of Chemistry, Zhejiang University, Hangzhou, 310027 China. 3 State Key Laboratory of Silicon Materials, Zhejiang University, Hangzhou, 310027 China. 4 Institute of Translational Medicine, Shanghai University, Shanghai, 200444 China"
    doc2 = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("text", "Yihao Sun, Liangrui He, Zhijun Shen, Yuxin Zhang, Yizhang Tang, Xujiang Yu", 1),
        ("text", affiliations, 1),
        ("text", "Abstract: " + ABSTRACT.replace("in vitro,", "in vitro as Smith et al. did,"), 1),
        ("text", "To cite this article: Jung U Shin, Mi Ryung Roh, Dong Kyun Rah, Nam Kyoung Ae, Hwal Suh & Kee Yang Chung (2011) The effect of succinylated atelocollagen and ablative fractional resurfacing laser on striae distensae, Journal of Dermatological Treatment, 22:2, 113-121, DOI: 10.3109/09546630903476902", 1),
        ("text", "Article views: 339", 1),
        ("section_header", "2 Materials and methods", 1), ("text", _vary(METHODS, 0), 1),
    ])
    tree2 = build_tree(doc2, "k")
    tops = [(n.heading, n.role, n.label) for n in tree2.root.children if n.type == "section"]
    assert tops == [("Front matter", "other", "section_header"), ("Abstract", "abstract", "section_header"), ("2 Materials and methods", "methods", "section_header")]
    assert "built_headings" not in tree2.repairs
    abstract = next(n for n in tree2.root.children if n.role == "abstract")
    assert len(abstract.children) == 1 and abstract.children[0].text.startswith("We report")
    front = tree2.root.children[0]
    assert [m.label for m in front.children] == ["authors", "affiliations", "notice", "other"]  # the citation line is a notice at any length


def test_an_unheaded_body_after_the_abstract_is_cut_by_the_block_lanes(fake_oracle):
    # Wiley's communications: abstract, then introduction, results and discussion with no heading at all
    # until "Experimental Section" — the first run after the abstract is the introduction, the rest are
    # what their paragraphs are, every heading the reader's
    doc = _doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("text", "John A. Smith 1 , Maria García 2 , Wei Zhang 1,*", 1),
        ("text", ABSTRACT, 1),
        ("text", CITING, 1), ("text", _vary(INTRO, 0), 1), ("text", _vary(INTRO, 1), 1),
        ("text", "Y. Alapan, M. Younesi, Prof. O. Akkus, Prof. U. A. Gurkan Mechanical and Aerospace Engineering Department Case Western Reserve University Cleveland, OH 44106, USA E-mail: umut@case.edu Department of Orthopaedics Case Western Reserve University Cleveland, OH 44106, USA", 1),
        ("text", _vary(RESULTS, 0), 1), ("text", _vary(RESULTS, 1), 2), ("text", _vary(RESULTS, 2), 2),
        ("text", _vary(DISCUSSION, 0), 2), ("text", _vary(DISCUSSION, 1), 2), ("text", _vary(DISCUSSION, 2), 2),
        ("section_header", "Experimental Section", 2), ("text", _vary(METHODS, 0), 2),
    ])
    tree = build_tree(doc, "k")
    tops = [(n.heading, n.role, n.label) for n in tree.root.children if n.type == "section"]
    assert tops[:2] == [("Front matter", "other", "section_header"), ("Abstract", "abstract", "section_header")]
    assert tops[-1] == ("Experimental Section", "methods", "section_header")
    built = [t for t in tops if t[2] == "built"]
    assert built[0] == ("Introduction", "introduction", "built") and ("Results", "results", "built") in built and ("Discussion", "discussion", "built") in built
    front = tree.root.children[0]
    assert [m.label for m in front.children] == ["authors", "correspondence"]  # the footnote block, whatever its length, is front matter (it carries the e-mail)
    assert [n.role for n in tree.walk() if n.type == "paragraph"] == ["abstract"] + ["introduction"] * 3 + ["results"] * 3 + ["discussion"] * 3 + ["methods"]


def test_above_the_title_the_dates_are_one_line_the_related_content_goes_and_a_footnote_stays():
    # IOP's first page: the label, the dates block one word or date per line, "You may also like" with other
    # papers' titles and authors, an ESI footnote, then the title and the paper
    doc = _doc([
        ("section_header", "PAPER", 1),
        ("text", "RECEIVED", 1), ("text", "21January 2015", 1), ("text", "REVISED", 1), ("text", "21 April 2015", 1), ("text", "ACCEPTED FOR PUBLICATION", 1), ("text", "8 May 2015", 1),
        ("text", "You may also like", 1),
        ("text", "Biofabrication of vascular networks", 1), ("text", "Wei Zhang, Xiaoyu Li, Marco Rossi et al", 1), ("text", "Bioprinting of cartilage", 1),
        ("footnote", "† Electronic supplementary information (ESI) available: Schematic representation, SDS-PAGE, fluorescence spectra and cell viability data.", 1),
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("text", "John A. Smith 1 , Maria García 2 , Wei Zhang 1,*", 1),
        ("section_header", "Abstract", 1), ("text", ABSTRACT, 1),
        ("section_header", "1. Introduction", 1), ("text", CITING, 1),
    ])
    tree = build_tree(doc, "k")
    front = tree.root.children[0]
    assert front.heading == "Front matter"
    assert [(m.label, m.text[:14]) for m in front.children] == [("notice", "PAPER"), ("dates", "RECEIVED\n21Jan"), ("other", "† Electronic s"), ("authors", "John A. Smith ")]
    assert front.children[1].text.count("\n") == 5  # six lines, one dates node
    assert tree.dropped == {"label": 3}  # "You may also like" and the lines under it (two of them joined by the pre-pass first)
    assert [(n.heading, n.role) for n in tree.root.children[1:] if n.type == "section"] == [("Abstract", "abstract"), ("1. Introduction", "introduction")]


def test_frontiers_first_page_keeps_the_papers_own_lines_and_drops_its_editors():
    # Frontiers' first page: editors and reviewers with their institutions, the correspondence, the dates,
    # the paper's own citation with its DOI, the copyright line, then the title, authors, affiliations, the
    # abstract, the keywords and the introduction
    citation = "Stamov S, Chobanov T, Wang T, Stoeva K and Reich D (2026) Genome-wide ancient DNA from the Bulgarian lands across three millennia. Front. Genet. 17:1864752. doi: 10.3389/fgene.2026.1864752"
    doc = _doc([
        ("text", "OPEN ACCESS", 1),
        ("text", "EDITED BY Beniamino Trombetta, Sapienza University of Rome, Italy", 1),
        ("text", "REVIEWED BY", 1), ("text", "Eugenia D'Atanasio, Sapienza University of Rome, Italy", 1), ("text", "Aurore Monnereau, University of Tartu, Estonia", 1),
        ("text", "*CORRESPONDENCE Svetoslav Stamov, stamovsvetoslav@gmail.com", 1),
        ("text", "RECEIVED 24 April 2026 REVISED 11 June 2026 ACCEPTED 16 June 2026 PUBLISHED 24 July 2026", 1),
        ("text", "CITATION", 1), ("text", citation, 1),
        ("text", "COPYRIGHT © 2026 Stamov, Chobanov, Wang, Stoeva and Reich. This is an open-access article distributed under the terms of the Creative Commons Attribution License (CC BY). The use, distribution or reproduction in other forums is permitted, provided the original author(s) and the copyright owner(s) are credited.", 1),
        ("title", "Genome-wide ancient DNA from the Bulgarian lands across three millennia", 1),
        ("text", "Svetoslav Stamov 1,2 *, Todor Chobanov 2,3 , Tianyi Wang 4,5 and David Reich 4", 1),
        ("text", "1 Laboratory of Interdisciplinary Research and Paleogenomic Knowledge, National Museum of History, Sofia, Bulgaria, 2 UNIBIT, Sofia, Bulgaria, 3 Institute of Balkan Studies, Sofia, Bulgaria, 4 Department of Genetics, Harvard Medical School, Boston, MA, United States", 1),
        ("text", "We report genome-wide ancient DNA from 37 individuals retained after conservative archaeological and genomic reassessment of 53 screened samples from the Bulgarian lands, spanning the Early Bronze Age to the Middle Ages, and dated to c. 350 CE at the latest, which we analyse together with published data from the region to trace the population history of the eastern Balkans in the first millennium.", 1),
        ("text", "KEYWORDS ancient DNA, Bulgaria, Balkans, population history, Bronze Age", 1),
        ("section_header", "1 Introduction", 1), ("text", CITING, 1),
        ("section_header", "2 Materials and methods", 2), ("text", _vary(METHODS, 0), 2),
    ])
    tree = build_tree(doc, "k")
    front = tree.root.children[0]
    assert front.heading == "Front matter"
    kinds = [(m.label, m.text[:20]) for m in front.children]
    assert kinds == [("correspondence", "*CORRESPONDENCE Svet"), ("dates", "RECEIVED 24 April 20"), ("notice", "CITATION\nStamov S, C"), ("authors", "Svetoslav Stamov 1,2"), ("affiliations", "1 Laboratory of Inte"), ("keywords", "KEYWORDS ancient DNA")]
    assert front.children[2].text.count("\n") == 2  # "CITATION", the citation, the copyright line: one notice node
    assert tree.dropped == {"label": 4}  # "OPEN ACCESS" (furniture), the editor, "REVIEWED BY" and the reviewers (two lines the pre-pass joined first)
    assert [(n.heading, n.role, n.label) for n in tree.root.children[1:] if n.type == "section"] == [("Abstract", "abstract", "section_header"), ("1 Introduction", "introduction", "section_header"), ("2 Materials and methods", "methods", "section_header")]
    assert [c.text[:9] for c in tree.root.children[1].children] == ["We report"] and "built_headings" not in tree.repairs


def test_a_main_text_wrapper_is_the_introduction_and_stands_top_level():
    # Nature's and OUP's JATS: the body opens with a section titled "Main" whose own paragraphs are the
    # introduction; the layout model nests it a level down, under the abstract
    doc = _doc([
        ("section_header", "Abstract", 1), ("text", ABSTRACT, 1),
        ("section_header", "Main", 1), ("text", CITING, 1), ("text", "Here we present a scaffold that is capable of load-bearing repair in the rabbit knee, and measure it.", 1),
        ("section_header", "Results across models", 1), ("text", _vary(RESULTS, 0), 1),
        ("section_header", "Methods", 2), ("text", _vary(METHODS, 0), 2),
    ])
    doc["texts"][2]["level"] = 2
    doc["texts"][5]["level"] = 2
    tree = build_tree(doc, "k")
    tops = [(n.heading, n.role, n.label, n.level) for n in tree.root.children if n.type == "section"]
    assert tops == [("Abstract", "abstract", "section_header", 1), ("Main", "introduction", "section_header", 1), ("Methods", "methods", "section_header", 1)]
    main = tree.root.children[1]
    assert [c.type for c in main.children] == ["paragraph", "paragraph", "section"] and main.children[2].heading == "Results across models"
    assert "built_headings" not in tree.repairs and [n.role for n in tree.walk() if n.type == "paragraph"] == ["abstract", "introduction", "introduction", "introduction", "methods"]


RSC_HEAD = "Carbon quantum dots have emerged as a versatile class of fluorescent carbon nanomaterials owing to their tunable optical properties, excellent chemical stability, low toxicity, and facile synthesis. Their photophysical behavior is generally"
RSC_TAIL = "attributed to the coexistence of graphitic domains, defect-related electronic states, and abundant surface functional groups, giving rise to broad absorption features and excitation-dependent photoluminescence. 1 - 3 These properties have stimulated extensive research on such dots for sensing and imaging."
RSC_INTRO2 = "Hybrid systems integrating these dots with molecular fluorophores have attracted increasing interest because their optical responses often differ from those of the individual components. 5 - 7 These mechanisms provide established frameworks for describing fluorescence modulation."
RSC_AUTHORS = "Do Dinh Trung, a Pham Van Duong, b Nguyen Minh Hoa, a Ho Ngoc Cuong b and Le Anh Thi * ab"


def test_a_title_read_after_the_introductions_first_lines_keeps_its_front_matter_and_its_introduction():
    # RSC's first page as the layout model reads it: the banner, the dates, the introduction's heading and
    # first lines (the left column), the affiliation footnotes, the licence, then the title, the authors,
    # the one-paragraph abstract, the rest of the introduction's first paragraph and its second
    doc = _doc([
        ("section_header", "RSC Advances", 1), ("section_header", "PAPER", 1),
        ("text", "Received 22nd August 2026 Accepted 26th August 2026", 1), ("text", "DOI: 10.1039/d6ra07899k", 1),
        ("section_header", "1. Introduction", 1), ("text", RSC_HEAD, 1),
        ("footnote", "a Institute of Tropical Durability, Joint Vietnam-Russia Tropical Science and Technology Research Center, Hanoi 100000, Vietnam", 1),
        ("footnote", "b Institute of Physics, Vietnam Academy of Science and Technology, Hanoi 100000, Vietnam", 1),
        ("text", "Licensed under CC-BY-NC 4.0", 1),
        ("section_header", "Excitation-dependent evolution of emissive states via interfacial electronic coupling in a carbon quantum dot hybrid system", 1),
        ("text", RSC_AUTHORS, 1), ("text", ABSTRACT, 1), ("text", RSC_TAIL, 1), ("text", RSC_INTRO2, 1),
        ("section_header", "2. Experimental", 2), ("text", _vary(METHODS, 0), 2),
        ("section_header", "3. Results and discussion", 2), ("text", _vary(RESULTS, 0), 2),
    ])
    tree = build_tree(doc, "k")
    assert tree.title.startswith("Excitation-dependent evolution")
    tops = [(n.heading, n.role, n.label, len(n.children)) for n in tree.root.children if n.type == "section"]
    assert tops == [("Front matter", "other", "section_header", 4), ("Abstract", "abstract", "section_header", 1), ("1. Introduction", "introduction", "section_header", 2), ("2. Experimental", "methods", "section_header", 1), ("3. Results and discussion", "results-discussion", "section_header", 1)]
    front = tree.root.children[0]
    assert [(m.label, m.text[:22]) for m in front.children] == [("notice", "RSC Advances\nPAPER"), ("dates", "Received 22nd August 2"), ("affiliations", "a Institute of Tropica"), ("authors", "Do Dinh Trung, a Pham ")]
    assert front.children[2].text.count("\n") == 1  # both affiliations, one line each
    intro = tree.root.children[2]
    assert intro.children[0].text.startswith("Carbon quantum dots") and "generally attributed to the coexistence" in intro.children[0].text and intro.children[1].text.startswith("Hybrid systems")
    assert tree.repairs.get("rejoined") == 1 and tree.repairs.get("reordered") == 3 and "built_headings" not in tree.repairs
    assert tree.dropped == {"label": 2}  # the DOI line and the licence line: furniture


def test_what_an_author_line_a_head_and_a_citation_are():
    from litrag_parser.tree import _CITES, _head_like, _looks_like_authors

    from litrag_parser.tree import _name_list

    assert _name_list("Shyrar Tanussiya Ramu, Madushika Dissanayake, Chamini Kanatiwela de Silva, Naduni Dasanthi and Ludo van der Berg")
    assert not _name_list("Collagen was extracted, purified and lyophilised") and not _name_list("Extraction, Gelation, and Applications of Collagen")
    assert _looks_like_authors(RSC_AUTHORS)
    assert _looks_like_authors("Anowarul Islam a , Thomas Mbimba a , Mousa Younesi b")
    assert not _looks_like_authors("d Le Quy Don Specialized High School, Dong Hai, Khanh Hoa 650000, Vietnam")  # an affiliation, markers or not
    assert not _looks_like_authors("Designing a Better, Stronger, Cheaper Scaffold")  # a title: its "a" is the article
    assert not _looks_like_authors("Received 22nd August 2026 Accepted 26th August 2026")  # a year is not a marker
    from litrag_parser.tree import _front_kind

    assert _front_kind("21January 2015", True, meaning=False) == "dates" and _front_kind("Available online 12 May 2015", True, meaning=False) == "dates"
    assert _front_kind("Supplementary material for this article is available online", True, meaning=False) != "dates"
    assert _front_kind("The authors have no conflicts of interest to disclose.", True, meaning=False) == "funding" and _front_kind("Funding: NIH R01 AR068426.", True, meaning=False) == "funding"
    assert _front_kind("This work proposes a multifunctional Bi2WO6:Yb,Er@CuS@CS nanocomposite that integrates photodynamic therapy and photothermal therapy for infected wounds.", True, meaning=False) != "correspondence"
    assert not _head_like("Licensed under CC-BY-NC 4.0") and not _head_like("Published on 01 September 2026")
    assert not _head_like("a Institute of Tropical Durability, Joint Vietnam-Russia Tropical Science and Technology Research Center, Hanoi 100000, Vietnam")
    assert not _head_like("Corresponding author. E-mail: leanhthi@duytan.edu.vn")
    assert not _head_like("Yihao Sun, Liangrui He, Zhijun Shen, Yuxin Zhang, Yizhang Tang, Xujiang Yu, Jisi Zheng")  # a JATS author list
    assert _head_like("The samples were sent to the university core facility, which") and _head_like(RSC_HEAD)
    assert _CITES.search("excitation-dependent photoluminescence (PL). 1 - 3 These properties") and _CITES.search("were reported earlier.4–6 Recent studies")
    assert _CITES.search("as shown before [12] and") and _CITES.search("(Smith et al. 2019)") and _CITES.search("was described (2019a) by")
    assert not _CITES.search("dissolved in 0.5 M NaCl at 4 °C") and not _CITES.search("see Fig. 3 for the") and not _CITES.search("at 37 °C for 24 h. Cells were") and not _CITES.search("dated to c. 350 CE and")
    from litrag_parser.tree import _affiliation_like, _citation_line

    assert _affiliation_like("1, Foot & Ankle Surgery, Department of Orthopaedics, Shanghai Sixth People's Hospital Affiliated to Shanghai Jiao Tong University School of Medicine, Shanghai, 200233 China. 2 Department of Chemistry, Zhejiang University")
    assert not _affiliation_like("Associations between wildfire smoke and cardiorespiratory emergency department visits varied by exposure product and referent period choice")
    assert _citation_line("Sablan, O., Ford, B., Hawkinson, C. B., et al. (2026). Wildfire smoke and cardiorespiratory emergency visits in New Mexico. GeoHealth, 10, e2025GH001492. https://doi.org/10.1029/2025GH001492")
    assert _front_kind("Stamov S, Chobanov T, Wang T and Reich D (2026) Genome-wide ancient DNA from the Bulgarian lands across three millennia. Front. Genet. 17:1864752. doi: 10.3389/fgene.2026.1864752", True, meaning=False) == "notice"
    assert not _citation_line("The data are available at https://doi.org/10.5281/zenodo.4897976 and were analysed with R.")
    assert _citation_line("Stamov S, Chobanov T, Wang T, Stoeva K, Toncheva DI, Lazaridis I and Reich D (2026) Paleogenomic evidence for genetic heterogeneity and prior admixture in Gothic-associated communities of late antique Bulgaria. Front. Genet. 17:1864752.")
    assert _citation_line("Sablan, O., Ford, B., Hawkinson, C. B., Hu, L., et al. (2026). Wildfire smoke and cardiorespiratory emergency visits in New Mexico. GeoHealth, 10, e2025GH001492.")
    assert not _citation_line("Smith and Jones (2019) reported that collagen gels stiffen with crosslinker content, which we confirm here.")
    assert _front_kind("Associations between wildfire smoke and cardiorespiratory emergency department visits varied by exposure product and referent period choice", True, meaning=False) != "affiliations"
    assert _front_kind("Department of Orthopaedic Surgery, Daejeon Eulji Medical Center, Eulji University School of Medicine, Daejeon, Korea", True, meaning=False) == "affiliations"
    assert _front_kind("Investigation performed at Case Western Reserve University, Cleveland, Ohio, USA", True, meaning=False) == "affiliations"
    assert _CITES.search("among the earliest microbial forms on Earth^1 and") and not _CITES.search("an area of 4 cm^2 was") and not _CITES.search("at 300 min^-1 for")


def test_viterbi_pays_to_switch_and_keeps_references_last():
    m = {"introduction": 0.5, "methods": 0.7, "results": 0.5, "results-discussion": 0.5, "discussion": 0.5, "references": 0.4, "back": 0.4}
    r = {**m, "methods": 0.5, "results": 0.7}
    ref = {**m, "methods": 0.5, "references": 0.8}
    assert viterbi([m, m, m, r, r, r]) == ["methods"] * 3 + ["results"] * 3
    r2 = {**m, "methods": 0.5, "results": 0.6}
    assert viterbi([m, m, r2, m, m], switch_cost=0.06) == ["methods"] * 5  # one block alone cannot pay for two switches
    assert viterbi([m, m, r, m, m], switch_cost=0.06)[2] == "results"  # unless its preference is worth more than both
    m9 = {**m, "methods": 0.9}
    assert viterbi([m, ref, ref, m9, m9]) == ["methods"] * 5  # nothing but back matter follows a reference list, so it cannot open in the middle
    assert viterbi([m, m, ref, ref])[-2:] == ["references", "references"]
    bk = {**m, "methods": 0.4, "back": 0.9}
    assert viterbi([m, m, ref, ref, bk, bk])[-2:] == ["back", "back"]  # declarations after the list are fine
    assert viterbi([]) == []


def test_a_fixture_paper_is_the_same_tree_with_and_without_the_oracle_where_its_headings_decide(fake_oracle):
    doc = json.loads((FIXTURES / "PMC3258128.docling.json").read_text("utf-8"))
    with_oracle = build_tree(doc, "k")
    lanes._active = None
    without = build_tree(doc, "k")
    assert [(n.heading, n.role) for n in with_oracle.root.children if n.type == "section" and n.role != "other"] == [(n.heading, n.role) for n in without.root.children if n.type == "section" and n.role != "other"]
    assert sum(v["asked"] for v in fake_oracle.summary()["kinds"].values()) > 0  # the oracle was consulted, and changed no lane a heading gave


def test_a_lane_lost_is_a_named_section_in_the_harness():
    before = [{"key": "k", "format": "pdf", "title": "t", "title_ok": True, "has_methods": True, "errors": 0, "citations": 10, "lanes": [["2 Fabrication", "methods"], ["3 Uses", "other"]]}]
    after = [{"key": "k", "format": "pdf", "title": "t", "title_ok": True, "has_methods": True, "errors": 0, "citations": 10, "error_kinds": {}, "lanes": [["2 Fabrication", "other"], ["3 Uses", "results"]]}]
    diff = compare(after, before)
    assert diff["lane_lost"] == ["k  '2 Fabrication': methods → other"] and diff["lane_gained"] == ["k  '3 Uses': other → results"]


# -- the reference list, when the page does not name it or does not keep it together -------------
# Every text below is a block as Docling read it out of the publisher's own PDF.

WILEY_ENTRIES = [
    "   L. Grande  ,    E.    Paillard  ,    J.    Hassoun  ,    J.-B.    Park  ,    Y .-J. Lee  ,    Y .-K.    Sun  , S.   Passerini  ,   B.   Scrosati  , Adv. Mater. 2015 , 27 ,   784  .",
    "   Y .   Lu  ,   M.   Tikekar  ,   R.   Mohanty  ,   K.   Hendrickson  ,   L.   Ma  ,   L. A.   Archer  , Adv. Energy Mater. 2015 , 5 ,   1402073  .",
    "   J. S. Dunning  ,    W. H.    Tiedemann  ,    L. Hsueh  ,    D. N.    Bennion  , J. Electrochem. Soc. 1971 , 118 ,   1886  .",
    "   A. S.   Arico  ,   P.   Bruce  ,   B.   Scrosati  ,   J.-M.   T arascon  ,   W .   van Schalkwijk  , Nat. Mater. 2005 , 4 ,   366  .",
    "   R. Cao  ,    W . Xu  , D.    Lv  , J. Xiao  ,    J.-G.    Zhang  , Adv.  Energy  Mater. 2015 , 5 ,   1402273  .",
    "   W . Xu  , J. Wang  , F. Ding  , X. Chen  , E. Nasybulin  , Y. Zhang  , J.-G.   Zhang  , Energy Environ. Sci. 2014 , 7 ,   513  .",
    "   Q.    Chen  ,    K.    Geng  ,    K.    Sieradzki  , J.  Electrochem.  Soc. 2015 , 162 , A2004  .",
    "   V .    Fleury  ,    J.  N.    Chazalviel  ,    M.    Rosso  ,    B.    Sapoval  , J.  Electroanal. Chem. Interfac. 1990 , 290 ,   249  .",
    "   J. N.   Chazalviel  , Phys. Rev. A 1990 , 42 ,   7355  .",
    "   E.    Peled  ,    D.    Golodnitsky  ,   G.   Ardel  , J.  Electrochem. Soc. 1997 , 144 , L208  .",
]

FRONTIERS_ENTRIES = [
    "Aia, P ., Wangchuk, L., Morishita, F., Kisomb, J., Y asi, R., and Kal, M. (2018). Epidemiology of tuberculosis in Papua New Guinea: analysis of case notification and treatment-outcome data,  2008-2016. West. Pac. Surveill. Response  J. 9,  2008-2016.  doi:  10.5365/ wpsar.2018.9.1.006",
    "Akapelwa, M. L., Kapalamula, T . F ., Moonga, L. C., Bwalya, P ., Solo, E. S., Chizimu, J. Y ., et al. (2025). Development of a multiplex loop-mediated isothermal amplification (LAMP) method for differential detection of Mycobacterium bovis and Mycobacterium tuberculosis. Microbiol. Spectr. 13. doi: 10.1128/spectrum.01234-25",
    "Aung, S. T., Thu, A., Aung, H. L., and Thu, M. (2021). Measuring catastrophic costs due to tuberculosis in Myanmar. Trop. Med. Infect. Dis. 6. doi: 10.3390/tropicalmed6030130",
    "Bhargava, A., and Bhargava, M. (2020). Tuberculosis deaths are predictable and preventable:  comprehensive  assessment  and  clinical  care  is  the  key. J.  Clin.  Tuberc.  Other Mycobact. Dis. 19:100155. doi: 10.1016/j.jctube.2020.100155",
    "Brynildsrud, O. B., Pepperell, C. S., Suffys, P ., Grandjean, L., Monteserin, J., Debech, N., et al. (2018). Global expansion of Mycobacterium tuberculosis lineage 4 shaped by colonial migration and local adaptation. Sci. Adv. 4:eaat5869. doi: 10.1126/sciadv.aat5869",
    "Buss, B. F., Keyser-metobo, A., Rother, J., Holtz, L., Gall, K., Jereb, J., et al. (2014). Possible airborne person-to-person transmission of Mycobacterium bovis - Nebraska, 2014-2015. MMWR Morb. Mortal Wkly. Rep. 65, 197-201. doi: 10.15585/mmwr.mm6508a1",
    "Croucher, N. J., Page, A. J., Connor, T. R., Delaney, A. J., Keane, J. A., Bentley, S. D., et al. (2015). Rapid phylogenetic analysis of large samples of recombinant bacterial whole genome sequences. Nucleic Acids Res. 43:e15. doi: 10.1093/nar/gku1196",
    "Didelot, X., and Wilson, D. J. (2015). ClonalFrameML: efficient inference of recombination in whole bacterial genomes. PLoS Comput. Biol. 11:e1004041. doi: 10.1371/journal.pcbi.1004041",
    "Diriba, G., Kebede, A., Tola, H. H., Alemu, A., Y enew, B., Moga, S., et al. (2021). Mycobacterial lineages associated with drug resistance in patients with extrapulmonary tuberculosis. Tuberc. Res. Treat. 2021:5511437. doi: 10.1155/2021/5511437",
    "Estaji, F ., Kamali, A., and Keikha, M. (2024). Strengthening the global response to tuberculosis: insights from the 2024 WHO global TB report. New Microbes New Infect. 62:101489. doi: 10.1016/j.nmni.2024.101489",
]

CONFLICT = "The  author(s)  declared  that  this  work  was  conducted  in  the absence of any commercial or financial relationships that could be construed as a potential conflict of interest."
PUBLISHER = "All claims expressed in this article are solely those of the authors and do not necessarily represent those of their affiliated organizations, or those of the publisher, the editors and the reviewers. Any product that may be evaluated in this article is not guaranteed or endorsed by the publisher."


def test_a_reference_list_the_layout_model_spaced_out_is_still_a_reference_list():
    # Docling reads this Wiley review's entries as "E.    Peled  ,    D.    Golodnitsky ,",
    # and with that spacing left in, no pattern for an entry matches one of them: the paper
    # arrived with 192 references and none of them in a reference list.
    body = [("text", _vary(DISCUSSION, n), 1) for n in range(3)] + [("list_item", e, 2) for e in WILEY_ENTRIES]
    tree = build_tree(_doc([("title", "A review of solid electrolyte interphases on lithium metal anode", 1), ("text", ABSTRACT, 1)] + body), "k")
    assert tree.repairs.get("inferred_references") == len(WILEY_ENTRIES)
    assert [n.role for n in tree.walk() if n.type == "list_item"] == ["references"] * len(WILEY_ENTRIES)


def test_the_entries_a_column_scatters_are_gathered_back_into_the_reference_list():
    # Frontiers sets the declarations in the column Docling reads between the first entry and
    # the rest: the list closes at "Conflict of interest" and every entry after it lands in
    # back matter. The entries are filed under the list; the declarations stay where they are.
    body = [
        ("section_header", "4 Discussion", 1), ("text", _vary(DISCUSSION, 0), 1), ("text", _vary(DISCUSSION, 1), 1),
        ("section_header", "References", 2), ("text", FRONTIERS_ENTRIES[0], 2),
        ("section_header", "Conflict of interest", 2), ("text", CONFLICT, 2),
        ("section_header", "Publisher's note", 2), ("text", PUBLISHER, 2),
    ] + [("text", e, 2) for e in FRONTIERS_ENTRIES[1:]]
    tree = build_tree(_doc([("title", "Genomic epidemiology of tuberculosis in Fiji", 1), ("text", ABSTRACT, 1)] + body), "k")
    refs = next(n for n in tree.root.children if n.type == "section" and n.role == "references")
    assert [c.text for c in refs.children] == FRONTIERS_ENTRIES
    assert tree.repairs.get("gathered_references") == len(FRONTIERS_ENTRIES) - 1
    assert {n.role for n in tree.walk() if n.type == "paragraph" and n.text in (CONFLICT, PUBLISHER)} == {"back"}
    assert "Publisher's note" in [c["why"] for c in tree.changes if c["kind"] == "gathered_references"][0]


def test_prose_after_a_reference_list_is_not_gathered_into_it():
    # the guard on the other side: an appendix that follows the list cites years and page
    # ranges like an entry does, and none of it belongs in the reference lane.
    appendix = [
        "Recruitment ran from March 2019 to November 2021, and the numbers in Table A1 are the counts at each visit, 118-127 of the 240 who were screened.",
        "The protocol was registered in 2019 and the analysis plan, which follows the 2020 revision, was fixed before any outcome data were seen.",
    ]
    body = [("section_header", "References", 2)] + [("text", e, 2) for e in FRONTIERS_ENTRIES] + [("section_header", "Appendix A", 3)] + [("text", t, 3) for t in appendix]
    tree = build_tree(_doc([("title", "Genomic epidemiology of tuberculosis in Fiji", 1), ("text", ABSTRACT, 1)] + body), "k")
    assert [n.text for n in tree.walk() if n.role == "references" and n.type == "paragraph"] == FRONTIERS_ENTRIES
    assert "gathered_references" not in tree.repairs
