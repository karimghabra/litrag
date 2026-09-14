"""MathML to text, and its insertion as <tex-math> before Docling reads the JATS."""

from pathlib import Path

from lxml import etree

from litrag_parser.mathml import mathml_to_text, with_tex_math

M = 'xmlns:mml="http://www.w3.org/1998/Math/MathML"'


def math(inner: str):
    return etree.fromstring(f"<mml:math {M}>{inner}</mml:math>")


def test_the_crosslinking_degree_equation():
    # from PMC11278924, section 2.4 — every letter of "Crosslinking Degree" is its own <mi>
    letters = "".join(f"<mml:mi>{c}</mml:mi>" for c in "Crosslinking") + "<mml:mo> </mml:mo>" + "".join(f"<mml:mi>{c}</mml:mi>" for c in "Degree")
    sub = lambda b, s: f"<mml:msub><mml:mi>{b}</mml:mi>{s}</mml:msub>"  # noqa: E731
    navg = "<mml:mrow><mml:mi>N</mml:mi><mml:mo>_</mml:mo><mml:mi>a</mml:mi><mml:mi>v</mml:mi><mml:mi>g</mml:mi></mml:mrow>"
    inner = (
        f"<mml:mrow>{letters}<mml:mo>=</mml:mo><mml:mn>100</mml:mn><mml:mo>×</mml:mo><mml:mfrac>"
        f"<mml:mrow><mml:mn>1</mml:mn><mml:mo>−</mml:mo><mml:mfrac><mml:mrow>{sub('A', '<mml:mi>x</mml:mi>')}</mml:mrow><mml:mrow>{sub('W', '<mml:mi>x</mml:mi>')}</mml:mrow></mml:mfrac></mml:mrow>"
        f"<mml:mrow><mml:mfrac><mml:mrow>{sub('A', navg)}</mml:mrow><mml:mrow>{sub('W', navg)}</mml:mrow></mml:mfrac></mml:mrow>"
        f"</mml:mfrac></mml:mrow>"
    )
    assert mathml_to_text(math(inner)) == "Crosslinking Degree=100×(1−A_x/W_x)/(A_{N_avg}/W_{N_avg})"


def test_scripts_roots_fences_and_accents():
    assert mathml_to_text(math("<mml:msup><mml:mi>x</mml:mi><mml:mn>2</mml:mn></mml:msup>")) == "x^2"
    assert mathml_to_text(math("<mml:msubsup><mml:mi>T</mml:mi><mml:mn>1</mml:mn><mml:mi>b</mml:mi></mml:msubsup>")) == "T_1^b"
    assert mathml_to_text(math("<mml:msqrt><mml:mi>a</mml:mi><mml:mo>+</mml:mo><mml:mi>b</mml:mi></mml:msqrt>")) == "√(a+b)"
    assert mathml_to_text(math("<mml:mroot><mml:mi>x</mml:mi><mml:mn>3</mml:mn></mml:mroot>")) == "root(x, 3)"
    assert mathml_to_text(math('<mml:mfenced open="[" close="]"><mml:mi>a</mml:mi><mml:mi>b</mml:mi></mml:mfenced>')) == "[a,b]"
    assert mathml_to_text(math("<mml:mover><mml:mi>x</mml:mi><mml:mo>¯</mml:mo></mml:mover>")) == "x̄"
    assert mathml_to_text(math("<mml:munder><mml:mo>lim</mml:mo><mml:mrow><mml:mi>n</mml:mi><mml:mo>→</mml:mo><mml:mi>∞</mml:mi></mml:mrow></mml:munder>")) == "lim_{n→∞}"
    # invisible operators vanish; the content half of <semantics> wins; whitespace collapses
    assert mathml_to_text(math("<mml:semantics><mml:mrow><mml:mi>f</mml:mi><mml:mo>⁡</mml:mo><mml:mi>x</mml:mi></mml:mrow><mml:annotation>f(x)</mml:annotation></mml:semantics>")) == "fx"


JATS = f"""<?xml version="1.0" encoding="UTF-8"?>
<article><body><sec><p>Determined using the equation below:</p>
<disp-formula id="e1"><label>(1)</label><mml:math {M}><mml:mrow><mml:mi>y</mml:mi><mml:mo>=</mml:mo><mml:msup><mml:mi>x</mml:mi><mml:mn>2</mml:mn></mml:msup></mml:mrow></mml:math></disp-formula>
<p>where <inline-formula><alternatives><mml:math {M}><mml:msub><mml:mi>A</mml:mi><mml:mi>x</mml:mi></mml:msub></mml:math></alternatives></inline-formula> is the absorbance and
<inline-formula><tex-math>$W_x$</tex-math><mml:math {M}><mml:mi>W</mml:mi></mml:math></inline-formula> the weight.</p></sec></body></article>"""


def test_tex_math_is_added_where_only_mathml_was():
    out = with_tex_math(JATS.encode("utf-8"))
    root = etree.fromstring(out)
    disp = root.find(".//disp-formula/tex-math")
    assert disp is not None and disp.text == "y=x^2"
    inline = root.findall(".//inline-formula")
    assert inline[0].find("tex-math").text == "A_x"  # added, beside the <alternatives>
    assert [t.text for t in inline[1].findall(".//tex-math")] == ["$W_x$"]  # the publisher's own is kept, none added
    assert b"<?xml" in out[:10] and "μ".encode("utf-8") not in out  # still UTF-8 bytes with a declaration


def test_bytes_that_are_not_xml_pass_through():
    assert with_tex_math(b"not xml at all") == b"not xml at all"
    plain = b'<?xml version="1.0"?><article><body><p>no maths</p></body></article>'
    assert with_tex_math(plain) is plain


SUPERSCRIPT_JATS = """<?xml version="1.0" encoding="UTF-8"?>
<article><body><sec><p>Damage to tendons is the most common injury.<sup><xref ref-type="bibr" rid="B1">1</xref></sup> More than half involve
ligaments.<sup><xref ref-type="bibr" rid="B2">2</xref>,<xref ref-type="bibr" rid="B3">3</xref></sup> As reviewed [<xref ref-type="bibr" rid="B4">4</xref>,<xref ref-type="bibr" rid="B5">5</xref>]
and by <xref ref-type="bibr" rid="B6">Smith et al. (2019)</xref>.</p></sec></body></article>"""


def test_numeric_xrefs_are_bracketed_unless_they_already_are():
    from litrag_parser.jats_prep import prepare_jats

    out = prepare_jats(SUPERSCRIPT_JATS.encode("utf-8"))
    text = " ".join("".join(etree.fromstring(out).find(".//p").itertext()).split())
    assert "injury.[1] More" in text and "ligaments.[2],[3] As" in text
    assert "[4,5]" in text and "[[4]" not in text  # the publisher's own brackets are kept as they were
    assert "Smith et al. (2019)" in text  # an author–year xref is left alone


def test_prepare_leaves_the_micromachines_xml_as_with_tex_math_did():
    from litrag_parser.jats_prep import prepare_jats

    from lxml import etree

    raw = (Path(__file__).parent / "fixtures" / "PMC11278924.xml").read_bytes()
    words = lambda b: [" ".join("".join(p.itertext()).split()) for p in etree.fromstring(b).iter("p")]  # noqa: E731
    assert words(prepare_jats(raw)) == words(with_tex_math(raw))  # MDPI brackets its citations itself: every paragraph reads the same
    assert b"<?cloudpmc" not in prepare_jats(raw)  # Europe PMC's processing instructions are gone


def test_processing_meta_is_dropped_so_docling_sees_jats_not_xhtml():
    from litrag_parser.jats_prep import prepare_jats

    raw = b'<?xml version="1.0"?><!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Archiving and Interchange DTD with MathML3 v1.4 20241031//EN" "JATS-archivearticle1-4-mathml3.dtd"><article dtd-version="1.4"><processing-meta base-tagset="archiving" mathml-version="3.0" table-model="xhtml" tagset-family="jats"><restricted-by>pmc</restricted-by></processing-meta><front/><body><p>Text.</p></body></article>'
    out = prepare_jats(raw)
    assert b"xhtml" not in out and b"processing-meta" not in out and b"<p>Text.</p>" in out and b"JATS-archive" in out


def test_empty_xrefs_take_their_entry_number_from_the_reference_list():
    from litrag_parser.jats_prep import prepare_jats

    refs = "".join(f'<ref id="ref{i}"><mixed-citation>Entry {i}</mixed-citation></ref>' for i in range(1, 16))
    raw = (
        '<?xml version="1.0"?><article><body><p>Enhance performance.<xref rid="ref14" ref-type="bibr"/> Some demand '
        'processes;<xref rid="ref5" ref-type="bibr"/>MINUS<xref rid="ref7" ref-type="bibr"/> and scarcity.'
        '<xref rid="ref3" ref-type="bibr"/>,<xref rid="ref4" ref-type="bibr"/></p></body><back><ref-list>' + refs + '</ref-list></back></article>'
    ).replace("MINUS", chr(0x2212)).encode("utf-8")
    out = prepare_jats(raw)
    text = " ".join("".join(etree.fromstring(out).find(".//p").itertext()).split())
    assert text == "Enhance performance.[14] Some demand processes;[5ENDASH7] and scarcity.[3],[4]".replace("ENDASH", chr(0x2013))
