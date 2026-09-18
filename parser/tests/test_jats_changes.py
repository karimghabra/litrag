"""What the XML pass says it did: one record per rewrite, in the shape the review reads.

`prepare_jats` rewrites a publisher's file before Docling sees it, and none of that shows
in the document that comes out. The log is the account of it; these tests hold it to the
shape `changes.py` expects and to one record per change, not per pass.
"""

from litrag_parser.changes import Repairs
from litrag_parser.jats_prep import prepare_jats

M = 'xmlns:mml="http://www.w3.org/1998/Math/MathML"'

FIELDS = {"kind", "stage", "page", "box", "before", "after", "why", "ref"}


def records(xml: str) -> list[dict]:
    log: list[dict] = []
    prepare_jats(xml.encode("utf-8"), log)
    return log


def of_kind(log: list[dict], kind: str) -> list[dict]:
    return [r for r in log if r["kind"] == kind]


XREFS = f"""<?xml version="1.0" encoding="UTF-8"?>
<article><body><sec><title>Results</title>
<p>Reported before<sup><xref ref-type="bibr" rid="b1">3</xref></sup> and since (<xref ref-type="bibr" rid="b2">4</xref>),
and again<xref ref-type="bibr" rid="b3"/>.</p></sec></body>
<back><ref-list><ref id="b1"><mixed-citation>Ito K. A first paper. J. Test. 2019;1:1-2.</mixed-citation></ref>
<ref id="b2"><mixed-citation>Ito K. A second paper. J. Test. 2020;2:3-4.</mixed-citation></ref>
<ref id="b3"><element-citation publication-type="journal"><person-group person-group-type="author">
<name><surname>Vogel</surname><given-names>A</given-names></name><etal/></person-group>
<article-title>Fields, not words</article-title><source>J. Test.</source><year>2021</year><volume>3</volume>
<fpage>5</fpage><lpage>9</lpage></element-citation></ref></ref-list></back></article>"""


def test_every_record_is_shaped_as_the_review_reads_it():
    log = records(XREFS)
    assert log, "the file is rewritten, so the log cannot be empty"
    for r in log:
        assert set(r) == FIELDS
        assert r["stage"] == "xml" and r["page"] is None and r["box"] is None
        assert r["why"].startswith(f"{r['kind']}: ") and r["why"].endswith(".")  # the rule names itself, in a sentence
        assert r["before"] is None or isinstance(r["before"], str)
        assert r["after"] is None or isinstance(r["after"], str)
        for side in ("before", "after"):
            assert r[side] is None or len(r[side]) <= 300


def test_the_review_takes_the_records_as_they_are():
    repairs = Repairs()
    repairs.extend(records(XREFS))
    assert repairs["bracket_numeric_xrefs"] == len(of_kind(records(XREFS), "bracket_numeric_xrefs"))
    assert all(entry["stage"] == "xml" for entry in repairs.log)  # the XML stage survives the counter's own map


def test_each_bracketed_xref_reports_itself():
    marks = of_kind(records(XREFS), "bracket_numeric_xrefs")
    assert [(r["before"], r["after"], r["ref"]) for r in marks] == [
        ("3", "[3]", "b1"),  # a bare superscript
        ("(4)", "[4]", "b2"),  # the publisher's own parentheses, made square
        (None, "[3]", "b3"),  # empty: the ordinal is the entry's place in the list
    ]
    assert "position" in marks[2]["why"] and "stylesheet" in marks[2]["why"]
    assert "round brackets" in marks[1]["why"]


def test_each_rendered_citation_reports_itself():
    rendered = of_kind(records(XREFS), "render_citations")
    assert len(rendered) == 1  # only the <ref> of fields; the two <mixed-citation> entries are left alone
    assert rendered[0]["ref"] == "b3"
    assert rendered[0]["before"] == "element-citation"
    assert rendered[0]["after"].startswith("Vogel A, et al. Fields, not words. J. Test. 2021;3:5-9")
    assert "element-citation" in rendered[0]["why"]


TITLES = f"""<?xml version="1.0" encoding="UTF-8"?>
<article><processing-meta tagset-family="jats" table-model="xhtml"/>
<front><article-meta><title-group><article-title>Growth of <italic>Morus alba</italic> L.</article-title></title-group></article-meta></front>
<body><p>A short communication with no section of its own, stated plainly.</p>
<sec id="s2"><label>2.</label><title>Materials and Methods</title>
<p>An image path sits here<?cloudpmc-path /var/pmc/1.jpg?>, invisibly.</p></sec>
<sec id="s3"><label>3.</label></sec></body></article>"""


def test_titles_labels_and_the_untitled_body_report_themselves():
    log = records(TITLES)
    flattened = of_kind(log, "flatten_titles")
    assert ("<italic>Morus alba</italic>" in flattened[0]["before"]) and flattened[0]["after"] == "Growth of Morus alba L."
    assert flattened[0]["ref"] == "article-title"
    folded = of_kind(log, "fold_section_labels")
    assert [(r["before"], r["after"], r["ref"]) for r in folded] == [
        ("Materials and Methods", "2. Materials and Methods", "s2"),
        ("<label>3.</label>", "3.", "s3"),  # a number and no words: the number is the heading
    ]
    wrapped = of_kind(log, "wrap_untitled_body")
    assert len(wrapped) == 1 and wrapped[0]["before"] is None and wrapped[0]["after"] == "Main text"
    assert wrapped[0]["ref"].startswith("A short communication")


def test_the_processing_bits_report_what_was_dropped():
    log = records(TITLES)
    pis = of_kind(log, "drop_processing_instructions")
    assert len(pis) == 1 and pis[0]["after"] is None
    assert pis[0]["before"] == "<?cloudpmc-path /var/pmc/1.jpg?>" and pis[0]["ref"] == "p/cloudpmc-path"
    meta = of_kind(log, "drop_processing_meta")
    assert len(meta) == 1 and meta[0]["after"] is None and 'table-model="xhtml"' in meta[0]["before"]
    assert "XHTML" in meta[0]["why"]


FORMULAS = f"""<?xml version="1.0" encoding="UTF-8"?>
<article><body><sec><title>Methods</title>
<p>The yield follows</p>
<disp-formula id="e1"><label>(1)</label><mml:math {M}><mml:mrow><mml:mi>y</mml:mi><mml:mo>=</mml:mo><mml:msup><mml:mi>x</mml:mi><mml:mn>2</mml:mn></mml:msup></mml:mrow></mml:math></disp-formula>
<disp-formula id="e2"><tex-math>\\documentclass{{article}}\\begin{{document}}$$ a = b $$\\end{{document}}</tex-math></disp-formula>
<disp-formula id="e3"><italic>k</italic><sub>obs</sub> = <italic>k</italic><sub>1</sub> + <italic>k</italic><sub>2</sub></disp-formula>
<p>where <inline-formula id="i1"><italic>C</italic><sub>t</sub></inline-formula> is the concentration.</p>
</sec></body></article>"""


def test_each_formula_line_reports_itself():
    log = records(FORMULAS)
    added = of_kind(log, "add_tex_math")
    assert [(r["after"], r["ref"]) for r in added] == [("y=x^2", "e1")]
    assert added[0]["before"] is None and "MathML" in added[0]["why"]
    rendered = {r["ref"]: r for r in of_kind(log, "render_formulas")}
    assert rendered["e2"]["after"] == "a = b" and "\\begin{document}" in rendered["e2"]["before"]
    assert rendered["e3"]["after"] == "k_obs = k_1 + k_2"
    assert rendered["i1"]["after"] == "C_t" and "<sub>t</sub>" in rendered["i1"]["before"]


IMAGE_FORMULA = """<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><sec><title>Methods</title>
<disp-formula id="e9"><graphic xlink:href="eq9.jpg"/></disp-formula></sec></body></article>"""


def test_a_formula_the_file_carries_only_as_a_picture_says_so():
    rendered = of_kind(records(IMAGE_FORMULA), "render_formulas")
    assert len(rendered) == 1 and rendered[0]["after"] == "[equation as image: eq9.jpg]"
    assert "picture" in rendered[0]["why"] and rendered[0]["ref"] == "e9"


LONG = """<?xml version="1.0" encoding="UTF-8"?>
<article><body><sec><title>Results</title><p>Text.</p></sec></body>
<back><ref-list><ref id="r1"><element-citation publication-type="journal">
<person-group person-group-type="author">%s</person-group>
<article-title>A paper with a great many authors</article-title><source>J. Test.</source><year>2022</year>
</element-citation></ref></ref-list></back></article>""" % "".join(
    f"<name><surname>Surname{i}</surname><given-names>A B</given-names></name>" for i in range(60)
)


def test_a_long_entry_is_clipped_not_carried_whole():
    rendered = of_kind(records(LONG), "render_citations")
    assert len(rendered) == 1
    assert len(rendered[0]["after"]) == 300 and rendered[0]["after"].endswith("…")


def test_the_bytes_are_the_same_whether_or_not_a_log_is_kept():
    for xml in (XREFS, TITLES, FORMULAS, IMAGE_FORMULA, LONG):
        raw = xml.encode("utf-8")
        assert prepare_jats(raw) == prepare_jats(raw, [])


def test_a_file_with_nothing_to_mend_records_nothing():
    plain = b'<?xml version="1.0" encoding="UTF-8"?>\n<article><body><sec><title>Results</title><p>Plain prose.</p></sec></body></article>'
    log: list[dict] = []
    assert prepare_jats(plain, log) is plain and log == []
    broken = b"not xml at all"
    assert prepare_jats(broken, log) is broken and log == []
