"""What kind of paper this is: the file's word first, then the record's, then the printed
label by meaning, then the paper's shape by meaning, else other — each verdict with its
source, and the audit expecting what the type promises."""

from litrag_parser import lanes
from litrag_parser.audit import audit_tree
from litrag_parser.paper_type import decide, from_jats, from_record, profile_of
from litrag_parser.store import open_store
from litrag_parser.tree import build_tree

from test_structure import _doc

JATS = b'<?xml version="1.0"?><!DOCTYPE article PUBLIC "-//NLM//DTD JATS" "x.dtd"><article xmlns:xlink="http://www.w3.org/1999/xlink" article-type="review-article" dtd-version="1.2"><front/></article>'


def _research():
    return build_tree(_doc([
        ("title", "A crosslinked collagen scaffold for tendon repair", 1),
        ("section_header", "Abstract", 1), ("text", "We report a crosslinked scaffold and measure its stiffness. The modulus rose with crosslinker content. Cells grew on it.", 1),
        ("section_header", "1 Introduction", 1), ("text", "Tendon injuries are common and heal slowly, which is why scaffolds have been studied for decades in this field.", 1),
        ("section_header", "2 Materials and methods", 1), ("text", "Collagen was cast and crosslinked with genipin for 24 h at room temperature before testing on the Instron.", 1),
        ("section_header", "3 Results", 2), ("text", "The compressive modulus rose from 12 to 48 kPa as the crosslinker concentration increased, as Figure 3 shows.", 2),
        ("section_header", "4 Discussion", 2), ("text", "Our findings demonstrate that the crosslinked scaffolds support cell growth while providing mechanical properties.", 2),
    ]), "k")


def _review():
    return build_tree(_doc([
        ("title", "REVIEW", 1),
        ("title", "Cellulose-based hydrogels for tissue engineering: a sustainable approach", 1),
        ("section_header", "Abstract", 1), ("text", "This review surveys the sources of cellulose, its derivatives and the hydrogels made from them, and their applications in tissue engineering.", 1),
        ("section_header", "1. Introduction", 1), ("text", "Cellulose is the most abundant polymer on earth and has been studied for decades in this field of work.", 1),
        ("section_header", "2. Cellulose Sources and Derivatives", 1), ("text", "Cellulose comes from plants, bacteria and tunicates, and each source gives a different fibril.", 1),
        ("section_header", "3. Synthesis of Cellulose-Based Hydrogels", 2), ("text", "Physical and chemical crosslinking both yield hydrogels, with different mechanics and degradation.", 2),
        ("section_header", "4. Conclusions and Future Perspectives", 2), ("text", "Cellulose hydrogels are promising, and several challenges remain before clinical use.", 2),
    ]), "k")


def test_the_files_word_comes_first():
    assert from_jats(JATS) == ("review", "review-article")
    assert from_jats(b"<article article-type='letter'>") == ("letter", "letter")
    assert from_jats(b"<article>") is None and from_jats(None) is None
    assert decide(_research(), jats_xml=JATS) == {"type": "review", "source": "jats", "detail": "review-article"}


def test_the_records_word_comes_next_most_specific_first():
    assert from_record(["review-article", "Review", "Journal Article"]) == ("review", "review-article; review; journal article")
    assert from_record("Journal Article; Case Reports") == ("case-report", "journal article; case reports")
    assert from_record(["Journal Article"]) == ("research", "journal article")
    assert from_record(["Published Erratum", "Journal Article"]) == ("correction", "published erratum; journal article")
    assert from_record(None) is None and from_record("") is None
    assert decide(_research(), pub_types="Journal Article; Review") == {"type": "review", "source": "record", "detail": "journal article; review"}


def test_the_printed_label_then_the_profile_by_meaning_and_other_when_unsure(fake_oracle):
    review = _review()
    assert [n.text for n in review.walk() if n.type == "meta" and n.label == "notice"] == ["REVIEW"]
    got = decide(review, oracle=fake_oracle)
    assert got["source"] == "printed" and got["type"] == "review" and got["detail"] == "REVIEW"
    research = _research()
    got = decide(research, oracle=fake_oracle)
    assert got["source"] in ("meaning", "none")  # nothing printed; the profile answers or does not
    assert profile_of(research).startswith("Title: A crosslinked collagen scaffold") and "Sections: Abstract; 1 Introduction; 2 Materials and methods; 3 Results; 4 Discussion" in profile_of(research)
    lanes._active = None
    assert decide(research) == {"type": "other", "source": "none", "detail": ""}


def test_the_audit_expects_what_the_type_promises():
    tree = build_tree(_doc([("title", "A short note on scaffolds and their many uses", 1), ("section_header", "Introduction", 1), ("text", "Scaffolds have been studied for decades in this field of work and many uses have been found for them.", 1)]), "k")
    assert not tree.has_methods
    kinds = {f.kind: f.severity for f in audit_tree(tree)}
    assert kinds.get("no-methods") == "info"
    assert {f.kind: f.severity for f in audit_tree(tree, "research")}.get("no-methods") == "warn"
    assert "no-methods" not in {f.kind for f in audit_tree(tree, "review")}
    assert "no-methods" not in {f.kind for f in audit_tree(tree, "letter")}


def test_the_type_is_a_column_and_an_old_store_grows_it(tmp_path):
    import sqlite3

    old = tmp_path / "store.sqlite"
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE papers (key TEXT PRIMARY KEY, doi TEXT, pmid TEXT, pmcid TEXT, title TEXT NOT NULL, file TEXT, sha256 TEXT, format TEXT, pages INTEGER, status TEXT NOT NULL DEFAULT 'queued', error TEXT, parser TEXT, added_at TEXT NOT NULL, parsed_at TEXT, seconds REAL, has_methods INTEGER)")
    c.execute("INSERT INTO papers(key, title, added_at) VALUES ('p', 't', '2026-01-01T00:00:00Z')")
    c.commit()
    c.close()
    conn = open_store(old)
    from litrag_parser.store import set_pub_types, set_type

    set_pub_types(conn, "p", ["Journal Article", "Review"])
    set_type(conn, "p", "review", "record", "journal article; review")
    row = conn.execute("SELECT type, type_source, pub_types FROM papers WHERE key = 'p'").fetchone()
    assert (row["type"], row["type_source"], row["pub_types"]) == ("review", "record", "Journal Article; Review")
