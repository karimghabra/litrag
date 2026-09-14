"""What a PDF's fonts did to its symbols, undone.

Older Wiley and Elsevier PDFs draw their operators from a Symbol font whose codes the
text layer maps through Latin-1 or through control codes, so the text Docling reads says
"pH ¼ 7.4" for "pH = 7.4", "medium þ 10%" for "medium + 10%", "37 \\x0e C" for "37 °C",
"p \\x14 0.05" for "p ≤ 0.05", "1 - 10 6" for "1 × 10⁶", a NUL for the minus of "Å⁻¹", and
in the oldest Wiley files digits for symbols: "37 8 C", "0.59 6 0.06", "N 5 3". Docling
itself sets an exponent apart ("10 7 cells") and splits the words whose ligature glyph
the font subset lacked ("signi fi cantly", "were fi xed"). These are the text layer's
errors, not the layout model's, and each has a context that makes it safe to repair: "¼"
between spaces is never a quarter in a paper, "þ" between spaces is never Icelandic,
"fi" is never a word. Anything outside those contexts is left alone.
"""

from __future__ import annotations

import re

# (pattern, replacement, what it is)
RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"(?<=[\w)\]%'’])\s*¼\s*(?=[\w(\[\-−+.])"), " = ", "Symbol '=' read as ¼: 'pH ¼ 7.4', 'n ¼ 3'"),
    (re.compile(r"(?<=[\w)\]%])\s+þ\s+(?=[\d(\[])"), " + ", "Symbol '+' read as þ before a number: 'medium þ 10%'"),
    (re.compile(r"(?<=[\w)\]%])\s*þ(?=[\s,.;)]|$)"), "+", "a trailing plus: 'CD4 þ and CD8 þ lymphocytes'"),
    (re.compile(r"(?<=\d)\s*-\s*10\s(\d{1,2})(?=\s|$|[a-zA-Z(])"), r" × 10^\1", "'1 - 10 6' for 1 × 10⁶: a multiplication sign read as a hyphen, the exponent set apart"),
    (re.compile(r"(?<=\S)\s*\x00\s*(?=\d)"), "^-", "a NUL where the minus of an exponent was: 'Å \\x00 1'"),
    (re.compile(r"\x00"), "", "any other NUL"),
    # Symbol-font glyphs that arrive as control codes, each read from its contexts in the corpus
    (re.compile(r"\s?[\x0e\x01]\s?(?=C\b|F\b)"), " °", "'37 \\x0e C' — the degree sign before its scale"),
    (re.compile(r"(?<=\d)\s?[\x0e\x01](?=\s|$|\))"), "°", "a degree sign after a number: 'an angle of 15 \\x0e'"),
    (re.compile(r"\s*\x01\s*(?=[A-Z])"), " · ", "'Tendon \\x01 Ligament' — the dot between a Springer paper's keywords"),
    (re.compile(r"(?<=\d)\s*\x06\s*(?=\d)"), " ± ", "'2.3 \\x06 0.3' — plus-minus"),
    (re.compile(r"\s*\x14\s*(?=[\d.])"), " ≤ ", "'p \\x14 0.05' — less than or equal"),
    (re.compile(r"\s*\x15\s*(?=[\d.])"), " ≥ ", "'tears with \\x15 5 cm' — greater than or equal"),
    (re.compile(r"\x18\s*(?=\d)"), "~", "'\\x18 20 µm' — approximately"),
    (re.compile(r"\x19\s*(?=\d)"), "≈", "'pH \\x19 7-8' — approximately equal"),
    (re.compile(r"\s*\x12\s*\x13"), "", "an empty bracket pair from the Symbol font: an equation drawn as an image, nothing to read"),
    (re.compile(r"(?<=[A-Za-z])\s?\x13\s?e(?=[a-z])"), "é", "'Or \\x13 efice' — an acute accent drawn apart from its e"),
    (re.compile(r"[\x01-\x08\x0b-\x1f\x7f]"), "", "any other control code"),
    # the oldest Wiley files map the Symbol font onto digits
    (re.compile(r"(?<=\d) 8 (?=C\b)"), " °", "'37 8 C' — the degree sign as an 8"),
    (re.compile(r"(?<=\d) 1 (?=C\b)"), " °", "'37 1 C' — the degree sign as a 1, in Elsevier's older Symbol map"),
    (re.compile(r"(?<=\b\d) 5 (?=(?:no|minimal|mild|moderate|severe|extensive|absent|present|none)\b)"), " = ", "'0 5 no presence' — equals as a 5 in a scoring scale"),
    (re.compile(r"(\d+\.\d+) 6 (?=\d+\.\d)"), r"\1 ± ", "'0.59 6 0.06' — plus-minus as a 6"),
    (re.compile(r"\b([Nn]) 5 (?=\d)"), r"\1 = ", "'N 5 3 threads/group' — equals as a 5"),
    (re.compile(r"\b1 3 (?=PBS|TBS|SSC|TAE|TBE|DPBS|HBSS)"), "1× ", "'1 3 PBS' — a multiplication sign as a 3"),
    (re.compile(r"\b([Pp]) \\ (?=\.\d|\d)"), r"\1 < ", "'P \\ .05' — less-than as a backslash"),
    (re.compile(r"\b(cm|nm|mm|m|s|min|h|mL|L|kg|g) 2 1\b"), r"\1^-1", "'960 cm 2 1' — a minus as a 2 before the exponent"),
    (re.compile(r"\b((?:CD|IL-|Ki-)\d{1,3}|SMA|Sca-1) 1 (?=cells?\b)"), r"\1+ ", "'IL-6 1 cells' — a plus as a 1 after a marker"),
    # digits set apart inside a number by an Elsevier text layer: "10,0 0 0", "80 0 0 kDa", "(20 03)"
    (re.compile(r"\b(\d{1,3}),(\d) (\d) (\d)\b"), r"\1,\2\3\4", "'10,0 0 0' — the thousands set apart"),
    (re.compile(r"\b(\d{1,3}) (\d) (\d)(?= (?:kDa|Da|events|cells|repeats|units|rpm|g|mg|µm|nm|×|X)\b)"), r"\1\2\3", "'80 0 0 kDa' — a number set apart before its unit"),
    (re.compile(r"\((\d{2}) (\d{2})\)"), r"(\1\2)", "'(20 03)' — a year set apart"),
    (re.compile(r"(?<=[A-Za-z]) (\d{2}) (\d) \((?=\d)"), r" \1\2 (", "'Science 30 0 (5625)' — a volume set apart"),
    (re.compile(r"(\d+) 3 (?=(?:Leica|Zeiss|Olympus|Nikon|objective|magnification|oil|water|air)\b)"), r"\1× ", "'63 3 Leica' — a multiplication sign as a 3 before an objective"),
    # an exponent Docling set apart with a space: "1 × 10 7 cells", "on the order of 10 5 neoblasts"
    (re.compile(r"(?<=[×x·] 10) (\d{1,2})\b"), r"^\1", "'1 × 10 6' — the exponent after a multiplication sign"),
    (re.compile(r"\b10 (\d)(?= (?:and|or|to|,|-|–) 10[ ^]\d)"), r"10^\1", "'10 7 and 10 5' — magnitudes in a row"),
    (re.compile(r"(10\^\d (?:and|or|to) 10) (\d)\b"), r"\1^\2", "the second of two magnitudes"),
    (re.compile(r"(?<=\b10) (\d{1,2})(?=\s+(?:cells?|CFU|copies|particles|molecules|neurons?|synapses|neoblasts|M\b|mM|µM|mL|ml|g\b|mg|kg|Pa|kPa|MPa|GPa|Hz|N\b|W\b|m\b|mm|cm|µm|nm|s\b|min|h\b|L\b|units?|fold|times|Da|kDa|Ω|V\b|per|/))"), r"^\1", "'10 7 cells' — a superscript set apart, before a unit"),
    (re.compile(r"(\b(?:of|to|and|or|than|from|about|approximately|around|over|under|below|above|between|~|≈|<|>|≤|≥|=) 10) (\d)(?=[\s,;.)]|$)(?![.,]\d)"), r"\1^\2", "'of 10 5' — a superscript set apart, after a word that expects a magnitude"),
]

_LIGATURES = ("ffi", "ffl", "ff", "fi", "fl")
_STOP = {
    "the", "a", "an", "of", "in", "to", "and", "or", "for", "with", "by", "on", "at", "as", "is", "are", "was", "were", "be", "been", "being", "has", "have", "had", "that", "this", "it", "its", "from", "not", "no", "we", "our", "their", "these", "those", "than", "then", "into", "onto", "upon", "over", "under", "per", "via", "if", "so", "up", "et", "al", "can", "could", "may", "might", "will", "would", "should", "must", "do", "does", "did", "when", "while", "where", "which", "who", "what", "how", "also", "both", "each", "all", "any", "some", "more", "most", "very", "only", "well", "such", "thus", "here", "there", "after", "before", "during", "between", "among", "within", "without", "about", "above", "below", "using", "used", "use", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "first", "second", "third", "last", "next", "new", "same", "other", "another", "further", "less", "least", "much", "many", "few", "several", "high", "low", "large", "small", "long", "short", "still", "yet", "again", "once", "twice", "just", "even", "always", "never", "often", "usually", "either", "neither", "nor", "but", "because", "since", "until", "unless", "although", "though", "whether", "however", "therefore", "hence", "whereas", "against", "through", "across", "along", "around", "toward", "towards", "near", "off", "out", "own", "per", "vs",
}
_PREFIXES = {"in", "de", "re", "un", "con", "pre", "pro", "sub", "non", "dis", "mis", "over", "under", "semi", "anti", "micro", "macro", "nano", "bio", "photo", "ultra", "inter", "intra", "trans", "super", "hyper", "hypo", "poly", "mono", "multi", "co", "ex", "e", "a", "o", "af", "ef", "of", "dif", "suf", "insuf", "ineff", "self", "cross", "counter", "auto", "para", "meta", "iso", "sur", "post", "peri", "endo", "exo", "epi"}
_COMMON = {
    "significant", "significantly", "significance", "insignificant", "specific", "specifically", "specificity", "specification", "specifications", "efficiency", "efficient", "efficiently", "efficacy", "sufficient", "sufficiently", "insufficient", "coefficient", "coefficients", "difference", "differences", "different", "differently", "differentiation", "differentiated", "differentiate", "differentiating", "diffusion", "diffusivity", "buffer", "buffered", "buffers", "stiffness", "stiff", "stiffer", "stiffening", "scaffold", "scaffolds", "effect", "effects", "effective", "effectively", "effectiveness", "affinity", "profile", "profiles", "profiling", "purification", "purified", "quantification", "quantified", "quantify", "identification", "identified", "identify", "magnification", "configuration", "configurations", "modification", "modifications", "modified", "unmodified", "confirm", "confirmed", "confirming", "confirmation", "define", "defined", "definition", "definitions", "artificial", "benefit", "benefits", "beneficial", "classification", "classified", "amplification", "amplified", "calcification", "calcified", "fibril", "fibrils", "fibrillar", "fibrillogenesis", "fibroblast", "fibroblasts", "fibrin", "fibrinogen", "fibrous", "fibrosis", "fibrotic", "fiber", "fibers", "fibre", "fibres", "fibronectin", "field", "fields", "final", "finally", "finding", "findings", "fine", "finite", "first", "fixed", "fixation", "five", "fixative", "influence", "influenced", "influences", "inflammation", "inflammatory", "infiltration", "infiltrated", "infection", "inflated", "inflation", "inflate", "fluorescence", "fluorescent", "fluid", "fluids", "flow", "flows", "flat", "flexible", "flexibility", "flexion", "reflect", "reflected", "reflects", "reflectance", "reflection", "deflection", "conflict", "conflicts", "influx", "efflux", "superficial", "filament", "filaments", "film", "films", "filter", "filtered", "filtration", "fill", "filled", "verification", "verified", "satisfied", "affect", "affected", "affects", "official", "differ", "differs", "differed", "diffuse", "diffusive", "difficult", "difficulty", "staff", "cliff", "safflower", "off", "offset", "effort", "efforts", "coffee", "cutoff", "stuff", "refined", "refine", "refinement", "deficiency", "deficient", "definitely", "refill", "reflux", "unfilled", "infill", "inflow", "outflow", "figure", "figures", "fit", "fits", "fitted", "fitting", "fixture", "fifth", "fifty", "fifteen", "finish", "finished", "firm", "firmly", "firing", "fish", "fistula", "flask", "flasks", "flange", "flap", "flaps", "flatten", "flattened", "flexural", "floor", "flush", "flushed", "fluorescein", "fluoride", "fluorine", "flux", "profound", "proficient", "sacrificed", "sacrifice", "unification", "unified", "specimen", "beneficiary",
}
_LETTER_RUN = re.compile(r"[A-Za-z]+|[^A-Za-z]+")
_GAPS = (" ", "  ", "\u00a0")


def ligature_vocabulary(texts) -> dict[str, set[str]]:
    """A document's own witnesses for the words its fonts split: `whole`, its words that carry
    a ligature unbroken ("significant" seen elsewhere); `real`, every word seen somewhere not
    beside a stray ligature token ("were", "the" — words, not fragments like "signi")."""
    whole: set[str] = set()
    real: set[str] = set()
    for text in texts:
        toks = _LETTER_RUN.findall(text)
        for i, tok in enumerate(toks):
            if not tok[0].isalpha():
                continue
            low = tok.lower()
            if tok in _LIGATURES or tok in ("Fi", "Fl", "Ff"):
                continue
            beside = (i >= 2 and toks[i - 1] in _GAPS and toks[i - 2] in _LIGATURES) or (i + 2 < len(toks) and toks[i + 1] in _GAPS and toks[i + 2] in _LIGATURES)
            if not beside:
                real.add(low)
            if any(lig in low for lig in ("fi", "fl", "ff")):
                whole.add(low)
    return {"whole": whole, "real": real}


def _known(word: str, vocabulary: dict[str, set[str]]) -> bool:
    low = word.lower()
    return low in vocabulary.get("whole", ()) or low in _COMMON or (low.endswith("s") and low[:-1] in _COMMON) or (low.endswith("ed") and low[:-2] in _COMMON) or (low.endswith("ly") and low[:-2] in _COMMON)


def repair_ligatures(text: str, vocabulary: dict[str, set[str]] | None = None) -> str:
    """"signi fi cantly" → "significantly", "were fi xed" → "were fixed", "speci fi c" →
    "specific", "sti ff ness" → "stiffness", "o ff" → "off". A stray ligature token joins the
    word beside it: both neighbours when the whole is a known word or the left one is a
    fragment; the right one when the left is a word in its own right."""
    low = re.sub(r"[ \u00a0]+", " ", text.lower())
    if not any(f" {lig} " in low or low.startswith(lig + " ") or low.endswith(" " + lig) for lig in _LIGATURES):
        return text
    vocab = vocabulary or {"whole": set(), "real": set()}
    toks = _LETTER_RUN.findall(text)
    i = 0
    while i < len(toks):
        tok = toks[i]
        if not (tok in _LIGATURES or tok in ("Fi", "Fl", "Ff")) or not tok[0].isalpha():
            i += 1
            continue  # lowercase, or capitalised at a sentence's start; "FF" is a fill factor, not a ligature
        left = toks[i - 2] if i >= 2 and toks[i - 1] in _GAPS and toks[i - 2][0].isalpha() else None
        right = toks[i + 2] if i + 2 < len(toks) and toks[i + 1] in _GAPS and toks[i + 2][0].isalpha() and toks[i + 2][0].islower() else None
        if left is not None and left.lower() in _LIGATURES:
            left = None  # two stray tokens in a row: the earlier one was already dealt with
        if right is not None and right.lower() in _STOP and len(right) <= 3 and not _known(tok + right, vocab):
            right = None  # "the fi in the figure" is nothing to mend; "between fl at" is
        whole = (left or "") + tok + (right or "")
        if left is not None and right is not None:
            real = vocab.get("real") or set()
            left_is_word = left.lower() in _STOP or (left.lower() in real and left.lower() not in _PREFIXES) or (not real and len(left) >= 7)
            if _known(whole, vocab):
                toks[i - 2 : i + 3] = [whole]  # "signi fi cantly"
                i -= 1
            elif _known(left + tok, vocab) and (left_is_word or not _known(tok + right, vocab)):
                toks[i - 2 : i + 1] = [left + tok]  # "cut o ff value"
                i -= 1
            elif left_is_word and left.lower() not in _PREFIXES:
                toks[i : i + 3] = [tok + right]  # "were fi xed"
            elif left.lower() in _PREFIXES and _known(tok + right, vocab) and left_is_word:
                toks[i : i + 3] = [tok + right]  # "in fixed positions"
            else:
                toks[i - 2 : i + 3] = [whole]  # "speci fi c": a fragment on the left
                i -= 1
            continue
        if right is not None:
            toks[i : i + 3] = [tok + right]
            continue
        if left is not None and (_known(left + tok, vocab) or left.lower() not in vocab.get("real", ()) or left.lower() in ("o", "sti", "cuto", "sta", "stu")):
            toks[i - 2 : i + 1] = [left + tok]
            i -= 1
            continue
        i += 1
    return "".join(toks)


def repair_glyphs(text: str, vocabulary: dict[str, set[str]] | None = None) -> str:
    """The text with the known font-mapping errors undone; unchanged when it has none."""
    if not text:
        return text
    out = text
    for pattern, repl, _ in RULES:
        out = pattern.sub(repl, out)
    return repair_ligatures(out, vocabulary)


_STRAY = re.compile(r"(?:^| )(?:ffi|ffl|ff|fi|fl)(?: |$)")


def glyph_residue(text: str) -> int:
    """How many suspect symbols remain — for the harness to count what the rules do not cover."""
    return sum(text.count(ch) for ch in "¼þ\x00\x0e\x01\x06\x18\x19\x12\x13\x14\x15") + len(_STRAY.findall(text))
