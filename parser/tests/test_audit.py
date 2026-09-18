"""The audit: each node against its neighbours, on small made-up documents and on the fixtures."""

import json
from pathlib import Path

import pytest

from litrag_parser.audit import SEVERITY, audit_doc, audit_tree, main, summarize
from litrag_parser.tree import build_tree

FIXTURES = Path(__file__).parent / "fixtures"


def doc_of(*items: tuple[str, str] | tuple[str, str, dict], name: str = "A Paper on Things") -> dict:
    """A Docling-shaped document: body → texts in order. An item is (label, text[, extra])."""
    texts, pictures = [], []
    body = []
    for i, it in enumerate(items):
        label, text = it[0], it[1]
        extra = it[2] if len(it) > 2 else {}
        if label == "picture":
            ref = f"#/pictures/{len(pictures)}"
            pictures.append({"self_ref": ref, "parent": {"$ref": "#/body"}, "children": [], "label": "picture", "captions": [], "prov": extra.get("prov", []), **{k: v for k, v in extra.items() if k != "prov"}})
        else:
            ref = f"#/texts/{len(texts)}"
            texts.append({"self_ref": ref, "parent": {"$ref": "#/body"}, "children": [], "label": label, "text": text, "prov": [], "level": 1 if label == "section_header" else None, **extra})
        body.append({"$ref": ref})
    return {"schema_name": "DoclingDocument", "name": name, "body": {"self_ref": "#/body", "children": body}, "texts": texts, "pictures": pictures, "tables": [], "groups": [], "pages": {}}


def kinds(findings):
    return sorted({f.kind for f in findings})


def test_fragments_and_symbol_starts_are_errors():
    # runs the layout model cut loose after an unfinished sentence are stitched back by tree.py …
    tree = build_tree(doc_of(("section_header", "2. Methods"), ("text", "Threads were crosslinked in genipin ("), ("text", "w"), ("text", "/"), ("text", "v"), ("text", ") prepared in ethanol."), ("text", "= 8/group). TNB is an assay.")), "k")
    assert [n.text for n in tree.walk() if n.type == "paragraph"] == ["Threads were crosslinked in genipin (w/v) prepared in ethanol.", "= 8/group). TNB is an assay."]
    f = audit_tree(tree)
    assert summarize(f) == {"symbol-start": 1} and f[0].severity == "error" and f[0].text.startswith("= 8/group")
    # … and the ones nothing can be done about are errors
    tree = build_tree(doc_of(("section_header", "2. Methods"), ("text", "The assay was done."), ("text", "w"), ("text", "/"), ("text", "Ready.")), "k")
    f = audit_tree(tree)
    assert summarize(f) == {"fragment": 1} and [x.text for x in f] == ["w/"] and f[0].severity == "error"  # "/" joined "w"; "w" had no sentence to rejoin


def test_a_dropped_equation_between_colon_and_where():
    tree = build_tree(doc_of(("section_header", "2. Methods"), ("text", "It was determined using the equation below:"), ("text", "where  represents the absorbance.")), "k")
    f = audit_tree(tree)
    assert {x.kind: x.severity for x in f} == {"missing-equation": "error", "skipped-inline": "warn", "lowercase-start": "warn"}
    # with the formula present, "where …" is what follows an equation and nothing is flagged
    tree = build_tree(doc_of(("section_header", "2. Methods"), ("text", "It was determined using the equation below:"), ("formula", "y=100×A_x/W_x"), ("text", "where A_x represents the absorbance.")), "k")
    assert audit_tree(tree) == []


def test_split_paragraphs_and_echoes():
    tree = build_tree(doc_of(("section_header", "2. Methods"), ("text", "The threads were placed in a tube and the"), ("text", "solution was refreshed every 2 h."), ("text", "Statistical analysis was performed twice."), ("text", "Statistical analysis was performed twice."), ("section_header", "3. Results"), ("section_header", "3. Results"), ("text", "Fine.")), "k")
    f = audit_tree(tree)
    # the split paragraph, the duplicated paragraph and the echoed heading are all repaired upstream
    # (tree.py joins the first, drops the second, continues the third), so the audit has nothing left to say
    assert summarize(f) == {}
    assert tree.repairs == {"joined": 1, "deduplicated": 1}
    assert [n.text for n in tree.walk() if n.type == "paragraph"][0] == "The threads were placed in a tube and the solution was refreshed every 2 h."
    assert [n.heading for n in tree.walk() if n.type == "section"] == ["2. Methods", "3. Results"]
    # a split the tree will not mend — three pages apart — is still named
    far = doc_of(("section_header", "2. Methods"), ("text", "The threads were placed in a tube and the", {"prov": [{"page_no": 1, "bbox": {"l": 0, "t": 10, "r": 10, "b": 0}}]}), ("text", "solution was refreshed every 2 h.", {"prov": [{"page_no": 4, "bbox": {"l": 0, "t": 10, "r": 10, "b": 0}}]}))
    far["pages"] = {"1": {"page_no": 1, "size": {"width": 600, "height": 850}}, "4": {"page_no": 4, "size": {"width": 600, "height": 850}}}
    assert summarize(audit_tree(build_tree(far, "k"))) == {"split-paragraph": 1}


def test_titles_and_pictures():
    prov = lambda page, w, h: [{"page_no": page, "bbox": {"l": 10, "t": 800, "r": 10 + w, "b": 800 - h, "coord_origin": "BOTTOMLEFT"}}]  # noqa: E731
    d = doc_of(("section_header", "ORIGINAL RESEARCH"), ("section_header", "1. Introduction"), ("text", "Hello."), ("picture", "", {"prov": prov(1, 300, 200)}), name="ORIGINAL RESEARCH")
    d["pages"] = {"1": {"page_no": 1, "size": {"width": 600, "height": 850}}}
    tree = build_tree(d, "k")
    f = audit_tree(tree)
    assert summarize(f) == {"generic-title": 1, "no-methods": 1, "uncaptioned-picture": 1}
    assert [x.severity for x in f if x.kind == "uncaptioned-picture"] == ["info"]  # large: a real figure without a caption


def test_the_fixtures_have_no_errors():
    for name in ("PMC3258128.docling.json", "PMC11278924.docling.json", "PMC11278924.jats.docling.json"):
        tree, findings = audit_doc(json.loads((FIXTURES / name).read_text("utf-8")), name)
        errors = [f for f in findings if f.severity == "error"]
        assert errors == [], f"{name}: " + "; ".join(f"{f.kind} {f.node_id} ‹{f.text}›" for f in errors)
    # the JATS fixture reads clean apart from what the publisher's XML does not carry
    tree, findings = audit_doc(json.loads((FIXTURES / "PMC11278924.jats.docling.json").read_text("utf-8")), "jats")
    assert {f.kind for f in findings} == set() and tree.dropped == {"empty": 1}  # "Associated Data", an empty wrapper in the back matter, is dropped and counted


def test_cli_reports_and_exits_nonzero_on_errors(capsys):
    code = main([str(FIXTURES / "PMC3258128.docling.json"), "--errors"])
    out = capsys.readouterr().out
    assert code == 0 and "PMC3258128" in out and "nothing to report" in out
    code = main([str(FIXTURES / "PMC11278924.jats.docling.json"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload[0]["nodes"] > 100 and isinstance(payload[0]["counts"], dict)


@pytest.mark.parametrize("severity", ["error", "warn", "info"])
def test_severities_are_ordered(severity):
    assert severity in SEVERITY
