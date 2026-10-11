"""What the JATS is told before Docling reads it.

Docling's JATS backend flattens a citation `<xref>` to its text and reads a formula only
from `<tex-math>`. What it would miss is made explicit in the bytes it gets — never in the file:

- every MathML-only formula gains a `<tex-math>` with the formula as a line of text
  (`mathml.py`), so the equation and its inline symbols survive;
- every numeric citation `<xref ref-type="bibr">` that is not already bracketed — a
  superscript "1" or "2,3" after a word — is bracketed, "[1]", "[2,3]", so the text says
  where a citation is and `citations.py` can tie it to its entry;
- `<processing-meta>` is dropped: Docling's format sniffer reads its `table-model="xhtml"`
  as XHTML and refuses the file;
- a section's `<label>` is folded into its `<title>`: Wiley and Elsevier write
  `<sec><label>2.</label><title>Materials and Methods</title>`, Docling takes the label as
  the heading and the words are lost, so "2." carries no role and the body files under
  Abstract. "2. Materials and Methods" carries one;
- every `<ref>` is given one `<mixed-citation>` of running text: an `<element-citation>`
  (Springer, some ACS) is fields Docling reads as nothing, and a `<ref>` with two
  citations (Wiley's "a)", "b)") became two entries, shifting every number after it;
- every float in the article's `<floats-group>` is set after the block of text that first
  cites it, a range "Tables 1–3" citing Table 2 too: Docling reads `<body>` and `<back>`
  only, so a paper that keeps its figures there (some of MDPI's, Hindawi's, Spandidos's,
  Ivyspring's) was read with none. A float nothing cites goes in a last section titled
  "(untitled section)", visibly the reader's;
- every figure and table is named as the page prints it: ACS writes its labels as a bare
  "1" and its calls as an empty `<xref>` or "Figure " with the number left to the
  stylesheet, so the label becomes "Figure 1" (or "Scheme 1", "Table 1") and the call
  takes its number; a label in markup becomes its text, since Docling reads a table's
  label as `.text` and a caption opened "None"; a `<table-wrap>` that is only an image
  becomes a `<fig>`, since Docling drops a table with no `<table>`, caption and all; a
  figure with no `<caption>` is given an empty one, since Docling closes such a caption
  with "None".

`floats_in` counts what the file holds, and `floats_note` is a reading's note when it has
fewer figures or tables than that, so a float lost is said and not silent.
"""

from __future__ import annotations

import re

from .mathml import add_tex_math

_NUMERIC_XREF = re.compile(r"\d{1,3}(?:\s*[,–-]\s*\d{1,3})*")
#: what joins the two ends of a range of calls, "1–3"
_DASHES = ("−", "–", "-", "—")


def bracket_numeric_xrefs(root) -> bool:
    """`<xref ref-type="bibr">2,3</xref>` becomes "[2,3]" unless a bracket already sits
    beside it. An empty `<xref rid="ref14" ref-type="bibr"/>` (ACS leaves the number to the
    stylesheet) gets the entry's position in the reference list, and two of them joined by
    a dash become one range. Author–year xrefs are left as they are."""
    changed = False
    position = {ref.get("id"): i + 1 for i, ref in enumerate(root.iter("ref")) if ref.get("id")}
    xrefs = [x for x in root.iter("xref") if x.get("ref-type") == "bibr"]
    removed: set = set()
    for xref in xrefs:
        if id(xref) in removed:
            continue
        text = "".join(xref.itertext()).strip()
        if not text and xref.get("rid") in position:
            text = str(position[xref.get("rid")])
            nxt = xref.getnext()
            if (xref.tail or "").strip() in _DASHES and nxt is not None and nxt.tag == "xref" and nxt.get("ref-type") == "bibr" and not "".join(nxt.itertext()).strip() and nxt.get("rid") in position:
                text = f"{text}–{position[nxt.get('rid')]}"
                xref.tail = nxt.tail
                removed.add(id(nxt))
                nxt.getparent().remove(nxt)
        if not text or not _NUMERIC_XREF.fullmatch(text):
            continue
        prev = xref.getprevious()
        parent = xref.getparent()
        before = (prev.tail if prev is not None else parent.text if parent is not None else "") or ""
        after = xref.tail or ""
        if before.rstrip().endswith("(") or after.lstrip().startswith(")"):
            # "(1)", "(1, 2)" in the publisher's own text: the parentheses become brackets, so
            # the text says the same thing in the one form the linker reads
            if before.rstrip().endswith("("):
                i = before.rstrip().rfind("(")
                new_before = before[:i] + "[" + before[i + 1 :]
                if prev is not None:
                    prev.tail = new_before
                elif parent is not None:
                    parent.text = new_before
            if after.lstrip().startswith(")"):
                i = after.find(")")
                xref.tail = after[:i] + "]" + after[i + 1 :]
            changed = True
            continue
        if before.rstrip().endswith("[") or after.lstrip().startswith("]") or (after.lstrip().startswith(",") and before.rstrip().endswith(",")):
            continue  # "[1,2]" in the publisher's own text, or the middle of such a list
        for child in list(xref):
            xref.remove(child)
        xref.text = f"[{text}]"
        changed = True
    return changed


def fold_section_labels(root) -> bool:
    """`<sec><label>2.</label><title>Materials and Methods</title>` → one title, "2. Materials and Methods"."""
    changed = False
    for sec in root.iter("sec"):
        label = sec.find("label")
        title = sec.find("title")
        if label is None or label.getparent() is not sec:
            continue
        number = " ".join(label.itertext()).strip()
        if title is None or title.getparent() is not sec:
            if number:
                label.tag = "title"  # a numbered section with no words: the number is all the heading there is
                changed = True
            continue
        if number:
            title.text = f"{number} {title.text or ''}".rstrip() if not (title.text or "").startswith(number) else title.text
        sec.remove(label)
        changed = True
    return changed


_CITATION_TAGS = ("mixed-citation", "element-citation", "nlm-citation")


def _text_of(el) -> str:
    return " ".join("".join(el.itertext()).split())


def _first(el, path: str) -> str:
    found = el.find(path)
    return _text_of(found) if found is not None else ""


def citation_text(el) -> str:
    """A citation as one line of text. A `<mixed-citation>` is its own words; an
    `<element-citation>` or `<nlm-citation>` is fields, rendered: authors, title, source,
    year;volume(issue):pages, doi."""
    if el.tag == "mixed-citation":
        return _text_of(el)
    groups = [g for g in el.iter("person-group")]
    authors_group = next((g for g in groups if g.get("person-group-type") in (None, "author")), groups[0] if groups else None)
    names: list[str] = []
    if authors_group is not None:
        for child in authors_group:
            if child.tag in ("name", "string-name"):
                surname, given = _first(child, "surname"), _first(child, "given-names")
                names.append(" ".join(x for x in (surname, given) if x) or _text_of(child))
            elif child.tag == "collab":
                names.append(_text_of(child))
            elif child.tag == "etal":
                names.append("et al.")
    else:
        for child in el:
            if child.tag in ("name", "string-name"):
                names.append(" ".join(x for x in (_first(child, "surname"), _first(child, "given-names")) if x) or _text_of(child))
    title = _first(el, "article-title") or _first(el, "chapter-title") or _first(el, "data-title") or _first(el, "part-title")
    source = _first(el, "source")
    year = _first(el, "year")
    volume, issue = _first(el, "volume"), _first(el, "issue")
    fpage, lpage = _first(el, "fpage"), _first(el, "lpage")
    elocation = _first(el, "elocation-id")
    publisher = ", ".join(x for x in (_first(el, "publisher-name"), _first(el, "publisher-loc")) if x)
    doi = next((_text_of(p) for p in el.iter("pub-id") if p.get("pub-id-type") == "doi"), "")
    pages = f"{fpage}-{lpage}" if fpage and lpage else fpage or elocation
    where = source
    if year:
        where = f"{where} {year}" if where else year
    if volume:
        where += f";{volume}"
        if issue:
            where += f"({issue})"
    if pages:
        where += f":{pages}" if volume else f" {pages}"
    parts = [", ".join(names), title, where, publisher, f"doi:{doi}" if doi else ""]
    return ". ".join(p.rstrip(".") for p in parts if p) + "."


def render_citations(root) -> bool:
    """Every `<ref>` holds one `<mixed-citation>` with the entry as text: an
    `<element-citation>` (fields, no running text — Docling reads it as nothing, or as a
    fragment) is rendered; a `<ref>` with several citations — Wiley's "a)", "b)"
    sub-references — becomes one, so the list Docling reads has one entry per `<ref>` and
    the numbering in the text still names the right entry; `<citation-alternatives>` keeps
    its text form. The metadata (`citations.py`) is read from the file itself, untouched."""
    from lxml import etree

    changed = False
    for ref in root.iter("ref"):
        cites = [c for c in ref if c.tag in _CITATION_TAGS or c.tag == "citation-alternatives"]
        if not cites or (len(cites) == 1 and cites[0].tag == "mixed-citation"):
            continue
        pieces: list[str] = []
        for c in cites:
            if c.tag == "citation-alternatives":
                alts = [a for a in c if a.tag in _CITATION_TAGS]
                mixed = [a for a in alts if a.tag == "mixed-citation"]
                chosen = mixed[0] if mixed else (alts[0] if alts else None)
                if chosen is not None:
                    pieces.append(citation_text(chosen))
            else:
                pieces.append(citation_text(c))
        text = " ".join(p for p in pieces if p)
        if not text:
            continue
        index = list(ref).index(cites[0])
        tail = cites[-1].tail
        for c in cites:
            ref.remove(c)
        new = etree.Element("mixed-citation")
        new.text = text
        new.tail = tail
        ref.insert(index, new)
        changed = True
    return changed


_LATEX_DOC = re.compile(r"^.*?\\begin\{document\}|\\end\{document\}.*$", re.S)
_LATEX_NOISE = re.compile(r"\$\$|\\begin\{align\*?\}|\\end\{align\*?\}|\\begin\{equation\*?\}|\\end\{equation\*?\}|\\displaystyle|\\nonumber|\\\\")
_MML = "{http://www.w3.org/1998/Math/MathML}math"


def _clean_tex(text: str) -> str:
    """The maths out of a whole LaTeX document (Springer's `<tex-math>` carries the preamble)."""
    t = _LATEX_DOC.sub("", text)
    t = _LATEX_NOISE.sub(" ", t)
    t = re.sub(r"\{\\text\{ ?\}\}|\\text\{ \}", " ", t)
    t = re.sub(r"\\text\{([^{}]*)\}", r"\1", t)
    return " ".join(t.split())


def _marked_text(el) -> str:
    """Italic, sub and sup markup as a line: ϕ_ex = ϕ_in × e^(−kd)."""
    out = []
    if el.text:
        out.append(el.text)
    for child in el:
        if not isinstance(child.tag, str):
            if child.tail:
                out.append(child.tail)
            continue  # a processing instruction or a comment: not words
        tag = etree_local(child.tag)
        if tag == "label":
            pass
        elif tag == "sub":
            out.append("_" + _marked_text(child).strip())
        elif tag == "sup":
            out.append("^" + _marked_text(child).strip())
        else:
            out.append(_marked_text(child))
        if child.tail:
            out.append(child.tail)
    return "".join(out)


def etree_local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def render_formulas(root) -> bool:
    """Every `<disp-formula>` and `<inline-formula>` holds a `<tex-math>` Docling reads:
    a LaTeX document is cut to its maths; `<alternatives>` is unwrapped; a formula that is
    only italic and sub/sup markup is set as a line; a formula the file carries only as an
    image gets a line that says so; an inline formula in a paragraph becomes its text in
    the paragraph; a display formula alone in a `<p>` is lifted out of it."""
    from lxml import etree

    changed = False
    for holder in list(root.iter("disp-formula", "inline-formula")):
        alt = holder.find("alternatives")
        if alt is not None:
            tex = alt.find("tex-math")
            math = alt.find(_MML)
            keep = tex if tex is not None else math
            if keep is not None:
                alt.getparent().replace(alt, keep)
                changed = True
        tex = holder.find("tex-math")
        if tex is not None:
            text = "".join(tex.itertext())
            cleaned = _clean_tex(text)
            if cleaned != text.strip():
                for child in list(tex):
                    tex.remove(child)
                tex.text = cleaned
                changed = True
        elif holder.find(_MML) is None:
            words = " ".join(_marked_text(holder).split())
            graphic = holder.find(".//graphic")
            if words and re.search(r"[=<>≈≤≥∝×/+−-]", words):
                line = words
            elif graphic is not None:
                href = graphic.get("{http://www.w3.org/1999/xlink}href") or ""
                line = f"[equation as image: {href}]" if href else "[equation as image]"
            else:
                line = ""
            if line:
                for child in list(holder):
                    holder.remove(child)
                holder.text = None
                tex = etree.SubElement(holder, "tex-math")
                tex.text = line
                changed = True
    for inline in list(root.iter("inline-formula")):
        parent = inline.getparent()
        if parent is None or etree_local(parent.tag) not in ("p", "td", "th", "title", "caption", "list-item"):
            continue
        if inline.find(".//tex-math") is not None or inline.find(".//" + _MML) is not None:
            continue  # Docling reads a tex-math inline; only markup-as-maths needs setting as text
        text = " ".join(_marked_text(inline).split())
        if not text and inline.find(".//inline-graphic") is not None:
            text = "[formula]"  # a symbol the file carries only as a picture: its place marked, not its name
        if not text:
            continue
        prev = inline.getprevious()
        tail = inline.tail or ""
        if prev is not None:
            prev.tail = (prev.tail or "") + text + tail
        else:
            parent.text = (parent.text or "") + text + tail
        parent.remove(inline)
        changed = True
    for disp in list(root.iter("disp-formula")):
        p = disp.getparent()
        if p is None or etree_local(p.tag) != "p":
            continue
        others = [c for c in p if c is not disp and etree_local(c.tag) != "label"]
        if (p.text or "").strip() or others or (disp.tail or "").strip():
            continue
        grand = p.getparent()
        if grand is None:
            continue
        grand.insert(list(grand).index(p) + 1, disp)
        disp.tail = p.tail
        grand.remove(p)
        changed = True
    for cite in root.iter("mixed-citation"):
        first = cite if cite.text and cite.text.strip() else next((d for d in cite.iter() if d is not cite and d.text and d.text.strip()), None)
        if first is not None and re.match(r"\s*[<>]+", first.text or ""):
            first.text = re.sub(r"^\s*[<>]+\s*", "", first.text)  # a stray ">" the publisher left before the authors
            changed = True
    return changed


_INLINE_TAGS = {"italic", "bold", "sub", "sup", "sc", "underline", "overline", "named-content", "monospace", "roman", "styled-content", "xref", "ext-link", "inline-formula", "abbrev", "break"}


def flatten_titles(root) -> bool:
    """Every title as one run of text. Docling's JATS backend reads a title's `.text` only:
    `<article-title>Ultrafiltered Mulberry (<italic>Morus alba</italic> L.)…` came out as
    "Ultrafiltered Mulberry (", and a section title that opens with `<italic>` — no text
    before the first child — crashed the backend into an empty document that reported
    success. Inline markup is folded into the text; an empty title gets an empty string, not
    None."""
    changed = False
    for title in list(root.iter("article-title", "title", "subtitle", "alt-title", "trans-title")):
        if len(title) == 0:
            if title.text is None:
                title.text = ""
                changed = True
            continue
        if all(etree_local(c.tag) in _INLINE_TAGS or not isinstance(c.tag, str) for c in title):
            text = " ".join("".join(title.itertext()).split())
            for c in list(title):
                title.remove(c)
            title.text = text
            changed = True
    return changed


#: what an article's `<floats-group>` holds that Docling reads when it stands in the text
_FLOATS = {"fig", "table-wrap", "table-wrap-group", "fig-group", "boxed-text"}
#: the parents of a block of text: a float is set after the block, never inside a list or a paragraph
_BLOCK_PARENTS = {"sec", "body", "app", "boxed-text"}
#: the heading of the section that holds the floats nothing cites — the reader's, not the author's
_UNTITLED = "(untitled section)"


def _in_front(el) -> bool:
    return any(etree_local(a.tag) == "front" for a in el.iterancestors())


def _article_groups(root) -> list:
    """The `<floats-group>`s that are the article's own: a sub-article's is not read either way."""
    return [g for g in root.iter("floats-group") if g.getparent() is not None and etree_local(g.getparent().tag) == "article"]


def _block_of(el):
    """The block of text an element stands in: itself or its nearest ancestor whose parent is a
    section, the body, an appendix or a box. None outside any — a footnote of the back matter."""
    while el is not None:
        parent = el.getparent()
        if parent is not None and etree_local(parent.tag) in _BLOCK_PARENTS:
            return el
        el = parent
    return None


def _family(el) -> str:
    """A float's kind for a range of calls: a figure group is figures, a table group tables."""
    return etree_local(el.tag).split("-")[0]


def place_floats(root) -> bool:
    """Every float of the article's `<floats-group>` set directly after the block of text that
    first cites it — the first `<xref>` in `<body>`, then in `<back>`, whose rid names the float
    or an id inside it, and that stands in a block (one in a footnote of the back matter places
    nothing) — and the emptied group removed. A range of calls joined by a dash, "Tables
    <xref>1</xref>–<xref>3</xref>", cites the floats of its kind between its ends in the group
    too, Table 2 that no xref names. Floats first cited in one block keep the group's order. A
    float nothing cites goes in a last body section titled "(untitled section)", so it is read
    and visibly not placed by its author; a body is made for it when the file has none."""
    from lxml import etree

    groups = _article_groups(root)
    floats = [f for g in groups for f in g if isinstance(f.tag, str) and etree_local(f.tag) in _FLOATS]
    if not floats:
        return False
    owner: dict = {}  # an id → the float that is it or holds it
    for f in floats:
        for el in f.iter():
            if isinstance(el.tag, str) and el.get("id"):
                owner.setdefault(el.get("id"), f)
    order = {f: i for i, f in enumerate(floats)}
    body, back = next(root.iter("body"), None), next(root.iter("back"), None)
    # keyed by the elements themselves, which the dicts keep alive: lxml hands back the same
    # proxy for a node only while one exists, so a key by id() could name another node later
    cited: dict = {}
    for scope in (body, back):
        if scope is None:
            continue
        for xref in scope.iter("xref"):
            named = [owner[rid] for rid in (xref.get("rid") or "").split() if rid in owner]
            nxt = xref.getnext()
            if named and (xref.tail or "").strip() in _DASHES and nxt is not None and etree_local(nxt.tag) == "xref" and nxt.get("ref-type") == xref.get("ref-type"):
                ends = [owner[rid] for rid in (nxt.get("rid") or "").split() if rid in owner]
                if ends:
                    named += [f for f in floats[order[named[-1]] + 1 : order[ends[0]]] if _family(f) == _family(named[-1])]
            for f in named:
                if f not in cited:
                    block = _block_of(xref)
                    if block is not None:
                        cited[f] = block
    last: dict = {}  # a block → the float last set after it
    uncited = []
    for f in floats:
        block = cited.get(f)
        if block is None:
            uncited.append(f)
            continue
        f.tail = None
        last.get(block, block).addnext(f)
        last[block] = f
    if uncited:
        if body is None:
            article = groups[0].getparent()
            body = etree.Element("body")
            article.insert(list(article).index(back if back is not None and back.getparent() is article else groups[0]), body)
        sec = etree.SubElement(body, "sec")
        etree.SubElement(sec, "title").text = _UNTITLED
        for f in uncited:
            f.tail = None
            sec.append(f)
    for g in groups:
        if not any(isinstance(c.tag, str) for c in g):
            g.getparent().remove(g)
    return True


_BARE_LABEL = re.compile(r"^\s*(S?\d+)\s*[.:]?\s*$")
_LABEL_NUMBER = re.compile(r"(S?\d+)\s*[.:]?\s*$")
_KIND_WORD = re.compile(r"(?i)(fig(?:ure)?s?\.?|tables?|schemes?|charts?)?")
_CALL_TYPES = {"fig", "table", "scheme", "chart"}
_NUMBER_FIRST = re.compile(r"\s*S?\d")


def _holds_table(el) -> bool:
    """Whether Docling finds a table in a `<table-wrap>`: a `<table>` of its own or in its `<alternatives>`."""
    return el.find("table") is not None or el.find("alternatives/table") is not None


def _kind_of(el) -> str:
    """The word a float's number is printed after: "Table" for a table; for a figure, "Scheme"
    or "Chart" when its fig-type or its id says it is one, else "Figure"."""
    if etree_local(el.tag) == "table-wrap":
        return "Table"
    kind, ident = (el.get("fig-type") or "").lower(), (el.get("id") or "").lower()
    if kind in ("scheme", "chart"):
        return kind.capitalize()
    if ident.startswith("sch"):
        return "Scheme"
    if ident.startswith(("cht", "chart")):
        return "Chart"
    return "Figure"


def _numbered_after(xref) -> bool:
    """Whether the file prints a call's number right after it: `<xref>Figure</xref> 1`, or
    `<xref>Fig.</xref><xref>1</xref>`."""
    tail = xref.tail or ""
    if _NUMBER_FIRST.match(tail):
        return True
    nxt = xref.getnext()
    return not tail.strip() and nxt is not None and etree_local(nxt.tag) == "xref" and _NUMBER_FIRST.match("".join(nxt.itertext())) is not None


def name_floats(root) -> bool:
    """Every figure and table outside `<front>` named as the page prints it, and every call to
    one carrying its number:

    (a) a `<label>` of inline markup becomes its text: Docling reads a table's label as `.text`,
        so `<label><bold>Table 1.</bold></label>` made a caption "NoneCurrent Advances…";
    (b) a label that is only a number, ACS's "1", is spelled out: "Table 1"; "Scheme 1" or
        "Chart 1" for a figure whose fig-type or id says it is one; "Figure 1" otherwise;
    (c) a call `<xref ref-type="fig|table|scheme|chart">` that holds no other xref and is empty
        or only a kind word ("Figure ": ACS leaves the number to its stylesheet) takes the
        number of the float its rid names, unless the file prints the number right after it.
        A panel letter ("d", "h–i") is never touched;
    (d) a `<table-wrap>` with no `<table>`, an image of one, becomes a `<fig>` with its id,
        label, caption and graphic: Docling drops a table it finds no `<table>` in, caption and all;
    (e) a `<fig>` with no `<caption>` is given an empty one: Docling joins the label to a caption
        it did not find as "Figure 1None", and to an empty one as "Figure 1"."""
    from lxml import etree

    changed = False
    numbers: dict[str, str] = {}
    for el in list(root.iter("fig", "table-wrap")):
        if _in_front(el):
            continue
        label = el.find("label")
        if label is not None:
            if len(label) and all(etree_local(c.tag) in _INLINE_TAGS or not isinstance(c.tag, str) for c in label):
                text = " ".join("".join(label.itertext()).split())
                for c in list(label):
                    label.remove(c)
                label.text = text
                changed = True
            bare = _BARE_LABEL.match("".join(label.itertext()))
            if bare:
                for c in list(label):
                    label.remove(c)
                label.text = f"{_kind_of(el)} {bare.group(1)}"
                changed = True
            number = _LABEL_NUMBER.search("".join(label.itertext()))
            if number and el.get("id"):
                numbers.setdefault(el.get("id"), number.group(1))
        if etree_local(el.tag) == "table-wrap" and not _holds_table(el):
            el.tag = "fig"
            changed = True
        if etree_local(el.tag) == "fig" and el.find("caption") is None:
            if label is not None:
                label.addnext(etree.Element("caption"))
            else:
                el.insert(0, etree.Element("caption"))
            changed = True
    for xref in list(root.iter("xref")):
        if xref.get("ref-type") not in _CALL_TYPES or xref.find(".//xref") is not None:
            continue
        text = "".join(xref.itertext())
        word = text.strip()
        if not _KIND_WORD.fullmatch(word) or _numbered_after(xref):
            continue
        found = list(dict.fromkeys(numbers[r] for r in (xref.get("rid") or "").split() if r in numbers))
        if not found:
            continue
        for c in list(xref):
            xref.remove(c)
        lead = text[: len(text) - len(text.lstrip())]
        # the number where the stylesheet would print it: "Figure " becomes "Figure 1", and
        # "Table " before a panel letter "Table 1A"
        xref.text = f"{lead}{word} {', '.join(found)}" if word else ", ".join(found)
        changed = True
    return changed


def floats_in(raw: bytes) -> dict[str, int] | None:
    """The figures and tables a JATS file holds where Docling reads once its floats are placed:
    every `<fig>` and `<table-wrap>` in the first `<body>`, the first `<back>` and the article's
    own `<floats-group>`, so never one under `<front>` (a graphical abstract). A `<table-wrap>`
    with no `<table>` counts as a figure, which is what `name_floats` makes it.
    `{figures, tables}`, or None for bytes that are not XML."""
    from lxml import etree

    try:
        root = etree.fromstring(raw, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=True))
    except etree.XMLSyntaxError:
        return None
    scopes = [s for s in (next(root.iter("body"), None), next(root.iter("back"), None)) if s is not None] + _article_groups(root)
    out = {"figures": 0, "tables": 0}
    for scope in scopes:
        for el in scope.iter("fig", "table-wrap"):
            out["tables" if etree_local(el.tag) == "table-wrap" and _holds_table(el) else "figures"] += 1
    return out


def _counted(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def floats_note(tree, raw: bytes | None) -> dict | None:
    """A reading's note when its tree holds fewer figures or fewer tables than the JATS file it
    was read from (`floats_in`): `{kind: "xml-floats", node_id, page, message}` on the paper's
    own node, for the audit and the paper's events. None when it holds them all, or for a PDF."""
    held = floats_in(raw) if raw else None
    if held is None:
        return None
    figures = sum(1 for n in tree.walk() if n.type in ("picture", "chart"))
    tables = sum(1 for n in tree.walk() if n.type == "table")
    if figures >= held["figures"] and tables >= held["tables"]:
        return None
    return {"kind": "xml-floats", "node_id": tree.root.node_id, "page": None,
            "message": f"the XML holds {_counted(held['figures'], 'figure')} and {_counted(held['tables'], 'table')}; this reading has {figures} and {tables}"}


def wrap_untitled_body(root) -> bool:
    """A body whose paragraphs stand before any `<sec>` (a letter, a case study, a short
    communication) files under the abstract, the last heading Docling saw. Those paragraphs
    are wrapped in a section titled "Main text" — a heading with no lane, which is what it is."""
    from lxml import etree

    changed = False
    for body in root.iter("body"):
        loose = []
        for child in body:
            if not isinstance(child.tag, str):
                continue
            if etree_local(child.tag) == "sec":
                break
            loose.append(child)
        if not any(etree_local(c.tag) == "p" for c in loose):
            continue
        sec = etree.Element("sec")
        title = etree.SubElement(sec, "title")
        title.text = "Main text"
        body.insert(0, sec)
        for c in loose:
            body.remove(c)
            sec.append(c)
        changed = True
    return changed


def drop_processing_instructions(root) -> bool:
    """Europe PMC's `<?cloudpmc-path …?>` inside every graphic: Docling reads the data of a
    processing instruction as text, so an image's storage path came out as a paragraph."""
    from lxml import etree

    changed = False
    for pi in list(root.iter(etree.ProcessingInstruction)):
        parent = pi.getparent()
        if parent is None:
            continue
        prev = pi.getprevious()
        if pi.tail:
            if prev is not None:
                prev.tail = (prev.tail or "") + pi.tail
            else:
                parent.text = (parent.text or "") + pi.tail
        parent.remove(pi)
        changed = True
    return changed


def drop_processing_meta(root) -> bool:
    """JATS 1.3+ opens with `<processing-meta … table-model="xhtml">`; it says nothing about
    the article and its "xhtml" makes Docling take the file for XHTML."""
    changed = False
    for meta in list(root.iter("processing-meta")):
        parent = meta.getparent()
        if parent is not None:
            parent.remove(meta)
            changed = True
    return changed


def _unfailing(step, root) -> bool:
    """A step that never costs a paper: one that stops half-way leaves well-formed XML, which is
    kept, and a float it could not set or name is read as the file has it, as before it."""
    try:
        return step(root)
    except Exception:  # noqa: BLE001 — never a paper lost to a float
        return True


def prepare_jats(raw: bytes) -> bytes:
    """The bytes Docling reads: formulas as text, numeric citations bracketed, floats where
    their text cites them. Anything that does not parse as XML is returned as it came."""
    from lxml import etree

    try:
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=True)
        root = etree.fromstring(raw, parser)
    except etree.XMLSyntaxError:
        return raw
    changed = drop_processing_instructions(root)
    changed = _unfailing(place_floats, root) or changed  # while an image of a table is still a table-wrap, so a range of tables spans it
    changed = _unfailing(name_floats, root) or changed  # before the titles are folded to text, so a call in a caption's title takes its number too
    changed = flatten_titles(root) or changed
    changed = add_tex_math(root) or changed
    changed = render_formulas(root) or changed
    changed = wrap_untitled_body(root) or changed
    changed = bracket_numeric_xrefs(root) or changed
    changed = render_citations(root) or changed
    changed = drop_processing_meta(root) or changed
    changed = fold_section_labels(root) or changed
    if not changed:
        return raw
    return etree.tostring(root.getroottree(), xml_declaration=True, encoding="utf-8")
