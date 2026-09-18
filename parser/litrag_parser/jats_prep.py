"""What the JATS is told before Docling reads it.

Docling's JATS backend flattens a citation `<xref>` to its text and reads a formula only
from `<tex-math>`. Two things are made explicit in the bytes it gets — never in the file:

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
  citations (Wiley's "a)", "b)") became two entries, shifting every number after it.

None of this is visible in the file on disk, so `prepare_jats(raw, log)` writes one record
per rewrite into `log` — what the element said, what it says now, and which rule and which
publisher habit are behind it — for the page-by-page review (`changes.py`). The XML is read
before there are pages, so a record's page and box are None. Without a log nothing is
recorded and the bytes are the same.
"""

from __future__ import annotations

import re

from .mathml import add_tex_math

_NUMERIC_XREF = re.compile(r"\d{1,3}(?:\s*[,–-]\s*\d{1,3})*")

_CLIP = 300


def _clip(text: str | None) -> str | None:
    """A record is read beside the element it belongs to, so a long run of text is cut short."""
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= _CLIP else text[: _CLIP - 1] + "…"


def _note(log: list[dict] | None, kind: str, *, before: str | None = None, after: str | None = None,
          why: str = "", ref: str | None = None) -> None:
    """One rewrite of the XML recorded, shaped as `changes.py` reads it. Every rule here works
    around something a publisher's file does, and the record says which rule and which
    behaviour, so a person reviewing the paper can tell a repair from a mistake."""
    if log is None:
        return
    log.append(
        {
            "kind": kind,
            "stage": "xml",
            "page": None,
            "box": None,
            "before": _clip(before),
            "after": _clip(after),
            "why": why,
            "ref": ref,
        }
    )


def _outline(el) -> str:
    """An element as it stood, markup and all, for the "before" side of a record."""
    from lxml import etree

    return " ".join(etree.tostring(el, encoding="unicode", with_tail=False).split())


def bracket_numeric_xrefs(root, log: list[dict] | None = None) -> bool:
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
        original = text
        from_position = False
        if not text and xref.get("rid") in position:
            text = str(position[xref.get("rid")])
            from_position = True
            nxt = xref.getnext()
            if (xref.tail or "").strip() in ("−", "–", "-", "—") and nxt is not None and nxt.tag == "xref" and nxt.get("ref-type") == "bibr" and not "".join(nxt.itertext()).strip() and nxt.get("rid") in position:
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
            _note(
                log,
                "bracket_numeric_xrefs",
                before=f"({text})",
                after=f"[{text}]",
                why="bracket_numeric_xrefs: the publisher wrote this citation marker in round brackets, which the linker does not read as a marker, so the parentheses are made square.",
                ref=xref.get("rid"),
            )
            continue
        if before.rstrip().endswith("[") or after.lstrip().startswith("]") or (after.lstrip().startswith(",") and before.rstrip().endswith(",")):
            continue  # "[1,2]" in the publisher's own text, or the middle of such a list
        for child in list(xref):
            xref.remove(child)
        xref.text = f"[{text}]"
        changed = True
        _note(
            log,
            "bracket_numeric_xrefs",
            before=original or None,
            after=f"[{text}]",
            why=(
                "bracket_numeric_xrefs: the publisher leaves the number of an empty <xref> to its own stylesheet, so the ordinal is taken from the entry's position in the reference list and bracketed."
                if from_position
                else "bracket_numeric_xrefs: a numeric citation marker carries no brackets of its own — a superscript in the publisher's file — so the text never said where a citation stood."
            ),
            ref=xref.get("rid"),
        )
    return changed


def fold_section_labels(root, log: list[dict] | None = None) -> bool:
    """`<sec><label>2.</label><title>Materials and Methods</title>` → one title, "2. Materials and Methods"."""
    why = "fold_section_labels: Wiley and Elsevier put the section number in a <label> of its own, which Docling takes for the whole heading, so the words of the title are lost and the body files under the abstract."
    changed = False
    for sec in root.iter("sec"):
        label = sec.find("label")
        title = sec.find("title")
        if label is None or label.getparent() is not sec:
            continue
        number = " ".join(label.itertext()).strip()
        if title is None or title.getparent() is not sec:
            if number:
                outline = _outline(label) if log is not None else None
                label.tag = "title"  # a numbered section with no words: the number is all the heading there is
                changed = True
                _note(log, "fold_section_labels", before=outline, after=number, why=why, ref=sec.get("id") or number)
            continue
        before = title.text or ""
        if number:
            title.text = f"{number} {title.text or ''}".rstrip() if not (title.text or "").startswith(number) else title.text
        sec.remove(label)
        changed = True
        _note(log, "fold_section_labels", before=before or None, after=title.text or "", why=why, ref=sec.get("id") or number or None)
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


def render_citations(root, log: list[dict] | None = None) -> bool:
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
        _note(
            log,
            "render_citations",
            before=" + ".join(c.tag for c in cites) if len(cites) > 1 else cites[0].tag,
            after=text,
            why=(
                "render_citations: this <ref> held several citations — Wiley's \"a)\", \"b)\" sub-references — which Docling counts as one entry each, shifting every number after it, so they are rendered into one."
                if len(cites) > 1
                else "render_citations: the entry is fields rather than running text (an <element-citation>, as Springer and some ACS write), which Docling reads as nothing, so it is rendered as a line."
            ),
            ref=ref.get("id"),
        )
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


def _formula_ref(holder) -> str | None:
    """Where a formula's record points: its id, else its printed number, else its kind."""
    label = holder.find("label")
    return holder.get("id") or (" ".join(label.itertext()).strip() if label is not None else None) or etree_local(holder.tag)


def render_formulas(root, log: list[dict] | None = None) -> bool:
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
                _note(
                    log,
                    "render_formulas",
                    before=text,
                    after=cleaned,
                    why="render_formulas: Springer writes a whole LaTeX document into <tex-math>, preamble and all, which Docling prints as it stands, so the formula alone is kept.",
                    ref=_formula_ref(holder),
                )
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
                ref = _formula_ref(holder)
                before = _outline(holder) if log is not None else None
                for child in list(holder):
                    holder.remove(child)
                holder.text = None
                tex = etree.SubElement(holder, "tex-math")
                tex.text = line
                changed = True
                _note(
                    log,
                    "render_formulas",
                    before=before,
                    after=line,
                    why=(
                        "render_formulas: the file carries this equation as a picture alone, so its place is marked in words rather than left blank."
                        if line.startswith("[equation as image")
                        else "render_formulas: the formula is nothing but italic and sub/sup markup, which Docling reads from no <tex-math> at all, so it is set down as one line."
                    ),
                    ref=ref,
                )
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
        before = _outline(inline) if log is not None else None
        ref = _formula_ref(inline)
        if prev is not None:
            prev.tail = (prev.tail or "") + text + tail
        else:
            parent.text = (parent.text or "") + text + tail
        parent.remove(inline)
        changed = True
        _note(
            log,
            "render_formulas",
            before=before,
            after=text,
            why="render_formulas: an inline symbol the publisher wrote as markup reaches Docling as nothing, leaving a sentence that reads \"where  represents\", so it is written into the paragraph as text.",
            ref=ref,
        )
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
            before = first.text
            first.text = re.sub(r"^\s*[<>]+\s*", "", first.text)  # a stray ">" the publisher left before the authors
            changed = True
            _note(
                log,
                "trim_citation_prefix",
                before=before,
                after=first.text,
                why="trim_citation_prefix: the publisher left a stray angle bracket in front of the authors of this entry, which reads as part of the first name.",
                ref=cite.getparent().get("id") if cite.getparent() is not None else None,
            )
    return changed


_INLINE_TAGS = {"italic", "bold", "sub", "sup", "sc", "underline", "overline", "named-content", "monospace", "roman", "styled-content", "xref", "ext-link", "inline-formula", "abbrev", "break"}


def flatten_titles(root, log: list[dict] | None = None) -> bool:
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
                _note(
                    log,
                    "flatten_titles",
                    before=None,
                    after="",
                    why="flatten_titles: an empty title reaches Docling's backend as None rather than a string, which it crashes on, so it is given an empty string.",
                    ref=etree_local(title.tag),
                )
            continue
        if all(etree_local(c.tag) in _INLINE_TAGS or not isinstance(c.tag, str) for c in title):
            before = _outline(title) if log is not None else None
            text = " ".join("".join(title.itertext()).split())
            for c in list(title):
                title.remove(c)
            title.text = text
            changed = True
            _note(
                log,
                "flatten_titles",
                before=before,
                after=text,
                why="flatten_titles: Docling's JATS backend reads a title's own text and none of its inline markup, so a title with an <italic> in it came out cut short, or empty and took the document with it.",
                ref=etree_local(title.tag),
            )
    return changed


def wrap_untitled_body(root, log: list[dict] | None = None) -> bool:
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
        opening = next((" ".join("".join(c.itertext()).split()) for c in loose if etree_local(c.tag) == "p"), "")
        _note(
            log,
            "wrap_untitled_body",
            before=None,
            after="Main text",
            why="wrap_untitled_body: a letter or short communication opens with paragraphs before its first <sec>, and Docling files them under the abstract, the last heading it saw, so they are given a heading of their own.",
            ref=_clip(opening[:80]) or None,
        )
    return changed


def drop_processing_instructions(root, log: list[dict] | None = None) -> bool:
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
        before = f"<?{pi.target} {pi.text or ''}?>"
        parent.remove(pi)
        changed = True
        _note(
            log,
            "drop_processing_instructions",
            before=before,
            after=None,
            why="drop_processing_instructions: Europe PMC puts a <?cloudpmc-path?> inside every graphic and Docling reads a processing instruction's data as text, so an image's storage path came out as a paragraph.",
            ref=f"{etree_local(parent.tag)}/{pi.target}",
        )
    return changed


def drop_processing_meta(root, log: list[dict] | None = None) -> bool:
    """JATS 1.3+ opens with `<processing-meta … table-model="xhtml">`; it says nothing about
    the article and its "xhtml" makes Docling take the file for XHTML."""
    changed = False
    for meta in list(root.iter("processing-meta")):
        parent = meta.getparent()
        if parent is not None:
            before = " ".join(f'{k}="{v}"' for k, v in meta.attrib.items())
            parent.remove(meta)
            changed = True
            _note(
                log,
                "drop_processing_meta",
                before=f"<processing-meta {before}>" if before else "<processing-meta>",
                after=None,
                why='drop_processing_meta: JATS 1.3 opens with a <processing-meta> whose table-model="xhtml" makes Docling\'s format sniffer take the file for XHTML and refuse it, and it says nothing about the article.',
                ref=meta.get("tagset-family") or "processing-meta",
            )
    return changed


def prepare_jats(raw: bytes, log: list[dict] | None = None) -> bytes:
    """The bytes Docling reads: formulas as text, numeric citations bracketed. Anything that
    does not parse as XML is returned as it came.

    `log`, when given, takes one record per rewrite — kind, before, after and the rule and
    publisher behaviour behind it — for the page-by-page review (`changes.py`). The XML pass
    knows no page and no box, so both are None; the bytes are the same either way."""
    from lxml import etree

    try:
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=True)
        root = etree.fromstring(raw, parser)
    except etree.XMLSyntaxError:
        return raw
    changed = drop_processing_instructions(root, log)
    changed = flatten_titles(root, log) or changed
    changed = add_tex_math(root, log) or changed
    changed = render_formulas(root, log) or changed
    changed = wrap_untitled_body(root, log) or changed
    changed = bracket_numeric_xrefs(root, log) or changed
    changed = render_citations(root, log) or changed
    changed = drop_processing_meta(root, log) or changed
    changed = fold_section_labels(root, log) or changed
    if not changed:
        return raw
    return etree.tostring(root.getroottree(), xml_declaration=True, encoding="utf-8")
