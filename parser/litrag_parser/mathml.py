"""MathML as a line of text, for Docling.

Docling's JATS backend reads a formula only from `<tex-math>`; Europe PMC's XML carries
MathML alone, so an equation vanished and the sentence around an inline symbol read
"where  represents". Before the XML reaches Docling, every `<mml:math>` whose formula
has no `<tex-math>` gets one, holding a plain linearisation — `A_x`, `100×(1−A_x/W_x)`,
`√(x)` — not LaTeX, but a line a person and an embedding can read. The file on disk is
never touched; only the bytes handed to Docling.
"""

from __future__ import annotations

import re

MATHML = "http://www.w3.org/1998/Math/MathML"
_INVISIBLE = {"⁡", "⁢", "⁣", "⁤"}  # function application, invisible times, …
_ACCENTS = {"¯": "̄", "‾": "̄", "―": "̄", "-": "̄", "^": "̂", "ˆ": "̂", "~": "̃", "˜": "̃", "˙": "̇", "→": "⃗", "⃗": "⃗"}
_ATOM = re.compile(r"[^\W_]+\.?[^\W_]*(?:[_^](?:[^\W_]|\{[^{}]*\}))*")
_TOKEN = re.compile(r"[^\W_]+\.?[^\W_]*")


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _script(s: str) -> str:
    """A sub- or superscript: braced unless it is one token."""
    return s if _TOKEN.fullmatch(s) else "{" + s + "}"


def _part(s: str) -> str:
    """A fraction's numerator or denominator: parenthesised unless it is one (scripted) token."""
    return s if _ATOM.fullmatch(s) else "(" + s + ")"


def linearize(el) -> str:  # noqa: C901 - one branch per MathML element
    """One MathML element as text."""
    tag = _local(el.tag)
    kids = [c for c in el if isinstance(c.tag, str)]
    parts = [linearize(k) for k in kids]
    if tag in ("mi", "mn", "mo", "mtext", "ms"):
        return "".join(ch for ch in (el.text or "") if ch not in _INVISIBLE)
    if tag == "mspace":
        return " "
    if tag in ("mphantom", "annotation", "annotation-xml"):
        return ""
    if tag == "semantics":
        return parts[0] if parts else ""
    if tag == "msub" and len(parts) == 2:
        return parts[0] + "_" + _script(parts[1])
    if tag == "msup" and len(parts) == 2:
        return parts[0] + "^" + _script(parts[1])
    if tag == "msubsup" and len(parts) == 3:
        return parts[0] + "_" + _script(parts[1]) + "^" + _script(parts[2])
    if tag == "mfrac" and len(parts) == 2:
        return _part(parts[0]) + "/" + _part(parts[1])
    if tag == "msqrt":
        return "√(" + "".join(parts) + ")"
    if tag == "mroot" and len(parts) == 2:
        return "root(" + parts[0] + ", " + parts[1] + ")"
    if tag in ("munder", "mover") and len(parts) == 2:
        accent = _ACCENTS.get(parts[1].strip())
        if tag == "mover" and accent and len(parts[0]) == 1:
            return parts[0] + accent
        return parts[0] + ("_" if tag == "munder" else "^") + _script(parts[1])
    if tag == "munderover" and len(parts) == 3:
        return parts[0] + "_" + _script(parts[1]) + "^" + _script(parts[2])
    if tag == "mfenced":
        given = el.get("separators")
        sep = "," if given is None else given.strip()[:1]
        return el.get("open", "(") + sep.join(parts) + el.get("close", ")")
    if tag == "mtable":
        return "[" + "; ".join(parts) + "]"
    if tag in ("mtr", "mlabeledtr"):
        return ", ".join(p for p in parts if p)
    if tag == "menclose":
        return "(" + "".join(parts) + ")"
    # math, mrow, mstyle, mpadded, mtd, merror and anything unknown: the children in order
    return "".join(parts)


def mathml_to_text(el) -> str:
    return re.sub(r"\s+", " ", linearize(el)).strip()


def add_tex_math(root) -> bool:
    """A `<tex-math>` beside every formula that has only MathML; whether anything changed."""
    from lxml import etree

    changed = False
    for math in root.iter("{%s}math" % MATHML):
        holder = math.getparent()
        while holder is not None and _local(holder.tag) not in ("disp-formula", "inline-formula"):
            holder = holder.getparent()  # through <alternatives>
        if holder is None or holder.find(".//tex-math") is not None:
            continue
        text = mathml_to_text(math)
        if not text:
            continue
        tex = etree.SubElement(holder, "tex-math")
        tex.text = text
        changed = True
    return changed


def with_tex_math(raw: bytes) -> bytes:
    """The JATS bytes with a `<tex-math>` added to every formula that has only MathML.

    Anything that does not parse as XML is returned as it came; Docling will say what it
    thinks of it.
    """
    from lxml import etree

    try:
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=True)
        root = etree.fromstring(raw, parser)
    except etree.XMLSyntaxError:
        return raw
    if not add_tex_math(root):
        return raw
    return etree.tostring(root.getroottree(), xml_declaration=True, encoding="utf-8")
