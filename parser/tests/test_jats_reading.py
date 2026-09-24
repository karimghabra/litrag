"""A JATS file read the way the file says: an abstract's parts, one paragraph each, and a
short bold paragraph as the subheading the publisher set without a <sec>."""

from litrag_parser.tree import build_tree


# ---- a JATS file read the way the file says: an abstract's parts, a bold line that is a heading ----


def _jats(texts):
    def jt(i, label, text, level=None, **extra):
        return {"self_ref": f"#/texts/{i}", "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "orig": text, "prov": [], **({"level": level} if level else {}), **extra}

    items = [jt(i, *t[:2], **(t[2] if len(t) > 2 else {})) for i, t in enumerate(texts)]
    return {"name": "d", "body": {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in items]}, "texts": items, "pictures": [], "tables": [], "groups": [], "pages": {}}


def test_a_jats_structured_abstract_is_read_one_paragraph_per_part():
    doc = _jats([("section_header", "Abstract", {"level": 1}),
                 ("text", "OBJECTIVE: Childhood epilepsy requires long-term therapy. METHODS: A retrospective study was conducted at one clinic (n = 100). RESULTS: The mean TSH rose from 1.46 to 1.60 mIU/L. CONCLUSION: The drug does not appear to affect thyroid function."),
                 ("section_header", "Introduction", {"level": 1}),
                 ("text", "Epilepsy is common in children. Thyroid hormones: they matter for growth, and METHODS: are not named here.")])
    tree = build_tree(doc, "k")
    abstract = [n.text for n in tree.walk() if n.type == "paragraph" and n.role == "abstract"]
    assert [a.split(":")[0] for a in abstract] == ["OBJECTIVE", "METHODS", "RESULTS", "CONCLUSION"] and tree.repairs.get("abstract_parts") == 4
    assert abstract[1] == "METHODS: A retrospective study was conducted at one clinic (n = 100)."
    assert len([n for n in tree.walk() if n.type == "paragraph" and n.role == "introduction"]) == 1  # only an abstract is cut at its labels


def test_an_abstract_that_opens_with_no_label_stays_one_paragraph():
    doc = _jats([("section_header", "Abstract", {"level": 1}),
                 ("text", "Tendons heal slowly. We tested two scaffolds in rabbits. Results: both improved the repair, and one more than the other.")])
    tree = build_tree(doc, "k")
    assert len([n for n in tree.walk() if n.type == "paragraph" and n.role == "abstract"]) == 1


def test_a_short_bold_jats_paragraph_is_a_subheading_in_its_sections_lane():
    bold = {"formatting": {"bold": True, "italic": False, "underline": False, "strikethrough": False, "script": "baseline"}}
    doc = _jats([("section_header", "Materials and Methods", {"level": 1}),
                 ("text", "Study population", bold),
                 ("text", "We enrolled twenty patients in the trial and followed them for a year."),
                 ("text", "This whole sentence is set in bold by the publisher for emphasis, and it is prose.", bold),
                 ("text", "The analysis followed the protocol.")])
    tree = build_tree(doc, "k")
    sub = [n for n in tree.walk() if n.type == "section" and n.heading == "Study population"]
    assert len(sub) == 1 and sub[0].role == "methods" and sub[0].level == 2 and tree.repairs.get("bold_headings") == 1
    assert [n.text[:4] for n in tree.walk() if n.type == "paragraph"] == ["We e", "This", "The "]  # a bold sentence stays prose
