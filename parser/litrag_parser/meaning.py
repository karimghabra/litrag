"""One oracle for every question of the form "which of these does this text resemble".

The reader decides most things by measuring the paper: line height, indents, repeated
lines, the paper's own words. What is left are questions of resemblance — which lane a
heading names, what kind of front matter a line is, whether a block beside a figure is
its caption, whether a line is a reference entry, which open paragraph a displaced tail
belongs to — and those used to be answered by lists of phrases that every new corpus
extended. Here each such question is a *kind*: a few named groups of example texts, a
threshold and a margin. A local embedder (nomic-embed-text through Ollama on
127.0.0.1) turns the text and the examples into vectors; the group whose examples lie
nearest names the text when the nearest is near enough and clearly nearer than the
next. Otherwise the answer is `other`: unassignable beats misassigned.

An embedder is not a language model: the same text gives the same vector, so every
answer is a deterministic function of the text, the examples and the model. Every
answer is a row in `lanes.sqlite` beside the libraries (`verdicts`), keyed by kind,
text and model — the model string carries a hash of the kind's examples, threshold,
margin and prefix, so editing any of them re-asks visibly rather than replaying rows
decided under another rule. A `rebuild` therefore needs no model, a second machine gives
the same tree, and a wrong answer is a row a person can read and delete. Two things are
not rows: raw scores (`scores`), whose caller stores what it decides from them; and
the `other` a text gets while the embedder is unreachable, which is not stored, so a
paper read while Ollama was down reads differently once it is up — the worker says so.

Nothing here reads a paper for a cloud service; the embedder is the one on this machine.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "nomic-embed-text"
QUERY, CLASSIFY = "search_query: ", "classification: "  # nomic's task prefixes


@dataclass(frozen=True)
class Verdict:
    """What the oracle said: the nearest group (or `other`), how near, and by how much."""

    name: str
    score: float = 0.0
    margin: float = 0.0

    @property
    def sure(self) -> bool:
        return self.name != "other"


@dataclass
class Kind:
    """A question: named groups of example texts, and how sure an answer must be. A kind
    may carry `centroids` instead of examples — one unit vector per group, computed once
    from many labelled texts (see `data/block_lanes.json`) — and a `position` prior: per
    group, a histogram of where in a paper its texts sit, in tenths."""

    name: str
    groups: dict[str, list[str]]
    threshold: float
    margin: float
    prefix: str = CLASSIFY
    centroids: dict[str, list[float]] | None = None
    position: dict[str, list[float]] | None = None
    source: str = ""
    embedder: str | None = None  # the model the centroids were made with; they mean nothing in another model's space
    prior_weight: float = 0.05
    prior_clamp: float = 0.1
    #: a wider margin for a group whose false positive costs more than a miss: `references`
    #: named on a review's "Literature search" swallows a section's citations
    lane_margin: dict[str, float] = field(default_factory=dict)

    @property
    def silent(self) -> bool:
        """A kind with nothing to compare against answers `other` to everything."""
        return not self.groups and not self.centroids

    def signature(self) -> str:
        """What a verdict depends on besides the text and the embedder: the examples (or the
        centroids and the prior), the threshold, the margin, the prefix. Any edit to them is
        a new model string, so the store re-asks instead of replaying a decision made under
        another rule."""
        what = [self.groups, self.threshold, self.margin, self.prefix, sorted(self.lane_margin.items())]
        if self.centroids is not None:
            what.append({g: [round(x, 5) for x in v] for g, v in self.centroids.items()})
            what.append([self.position, self.prior_weight, self.prior_clamp, self.embedder])
        return hashlib.sha1(json.dumps(what, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:8]

    def prior(self, group: str, at: float | None) -> float:
        """A nudge from where in the paper a text sits: `prior_weight` × log of how much
        likelier than uniform this group is at that tenth, clamped to ±`prior_clamp` so it
        can tip a close call and never decide one alone. Nothing when the kind has no prior
        or the caller gave no position."""
        if self.position is None or at is None or group not in self.position:
            return 0.0
        hist = self.position[group]
        p = hist[min(max(int(at * len(hist)), 0), len(hist) - 1)]
        return max(-self.prior_clamp, min(self.prior_clamp, self.prior_weight * math.log(max(p * len(hist), 1e-6))))


# -- the questions, as data -----------------------------------------------------------------

HEADING_PROTOTYPES: dict[str, list[str]] = {
    "introduction": ["Introduction", "Background", "Background and objectives", "Objectives", "Aims of the study"],
    "methods": ["Methods", "Materials and methods", "Experimental section", "Study design and participants", "Methodology", "Experimental procedures", "Data collection and analysis", "Statistical analysis"],
    "results": ["Results", "Findings", "Experimental results", "Observations"],
    "results-discussion": ["Results and discussion"],
    "discussion": ["Discussion", "Conclusions", "Conclusion and outlook", "Limitations", "Implications", "Strengths and limitations", "Summary and future directions"],
    "back": ["Acknowledgements", "Author contributions", "Funding", "Conflict of interest", "Data availability statement", "Ethics approval", "Supplementary material", "Footnotes", "Declarations", "Competing interests", "Abbreviations", "Author information"],
}

_PROSE_STARTS = [
    "The samples were then washed three times with PBS and incubated at 37 °C for 24 h before seeding.",
    "In this study we investigated whether the crosslinked network restricts swelling while preserving porosity.",
    "These results suggest that the crosslinker, rather than the collagen concentration, governs stiffness.",
    "Cells were cultured in DMEM supplemented with 10% FBS and 1% penicillin–streptomycin.",
    "However, the earlier studies used higher concentrations and shorter follow-up.",
    "A total of 24 rabbits were randomly assigned to three groups of eight.",
    "Previous studies have reported similar trends for gelatin methacrylate hydrogels [21,22].",
    "As shown in Figure 3, the modulus increased with crosslinker concentration up to 2 wt%.",
]

_ENTRIES = [
    "[12] Smith JA, Lee CD, Brown EF. Collagen crosslinking in tendon repair. J Biomed Mater Res A. 2019;107(4):812-821.",
    "3. Zhang W, Patel RK, Rossi S, et al. Hydrogel scaffolds for cartilage regeneration: a review. Biomaterials 2020, 245, 119975.",
    "Müller, H.; Nowak, T.; Tanaka, Y. Injectable hydrogels for minimally invasive delivery. Adv. Mater. 2021, 33, 2004083.",
    "García-López, M., & O'Connor, D. (2018). Mechanical testing of soft tissues. Journal of the Mechanical Behavior of Biomedical Materials, 84, 1–12. https://doi.org/10.1016/j.jmbbm.2018.04.001",
    "J. A. Smith, C. D. Lee, E. F. Brown, Nature 2017, 543, 123–127.",
    "Natarajan P, Jones AB (2016) Biomarkers of osteoarthritis progression. Osteoarthritis Cartilage 24:1–9",
    "Brown E, Smith J. Scaffold design. In: Editor A, ed. Principles of Tissue Engineering. 2nd ed. New York: Springer; 2015. p. 45-67.",
    "17 Y. Tanaka, H. Müller and T. Nowak, Chem. Soc. Rev., 2020, 49, 1234–1256.",
]

#: a few paragraphs per lane: what the tests use, in a toy embedder's space. Measured against
#: real headings they were near chance (NOTES.md, 2026-09-13), so no verdict rests on them.
BLOCK_EXAMPLES: dict[str, list[str]] = {
        "introduction": [
            "Osteoarthritis is the most common joint disease worldwide and a leading cause of disability in older adults [1]. Despite decades of research, no disease-modifying treatment is available, and current management is limited to symptom control.",
            "Hydrogels have attracted considerable interest as scaffolds for tissue engineering because their high water content and tunable mechanics resemble native extracellular matrix [3–5]. However, most natural hydrogels lack the mechanical strength required for load-bearing applications.",
            "Here we report a crosslinking strategy that addresses this limitation. We hypothesised that a short crosslinker would stiffen the network without reducing porosity. The aim of this study was to determine whether such scaffolds support chondrocyte growth.",
        ],
        "methods": [
            "Type I collagen was extracted from rat tail tendons as previously described [14]. Briefly, tendons were dissolved in 0.02 M acetic acid at 4 °C for 48 h, centrifuged at 10,000 g for 30 min, and the supernatant was lyophilised.",
            "Human bone marrow-derived mesenchymal stem cells (Lonza, Basel, Switzerland) were cultured in DMEM supplemented with 10% FBS and 1% penicillin–streptomycin at 37 °C in 5% CO2. Cells at passage 4 were seeded at 1 × 10^4 cells/cm².",
            "Statistical analysis was performed using GraphPad Prism 9. Data are presented as mean ± standard deviation. Differences between groups were assessed by one-way ANOVA with Tukey's post hoc test; p < 0.05 was considered significant.",
            "Participants were recruited from three outpatient clinics between January 2018 and December 2019. Inclusion criteria were age 18–65 years and a confirmed diagnosis. The study was approved by the institutional review board and all participants gave written informed consent.",
        ],
        "results": [
            "The compressive modulus increased from 12 ± 3 kPa to 48 ± 6 kPa as the crosslinker concentration rose from 0.5 to 2 wt% (Figure 3a, p < 0.01). The swelling ratio decreased correspondingly (Table 1).",
            "Live/dead staining showed more than 90% viable cells on all scaffolds at day 7 (Figure 4b). Cell number was significantly higher on the crosslinked scaffolds than on controls (p = 0.003).",
            "Of the 240 participants enrolled, 212 completed follow-up. Mean age was 54.2 years (SD 9.8) and 61% were women. The primary outcome occurred in 18% of the intervention group versus 29% of controls (relative risk 0.62, 95% CI 0.41–0.94).",
        ],
        "results-discussion": [
            "The modulus increased with crosslinker content (Figure 3), which we attribute to the denser network; a similar trend was reported for gelatin methacrylate [21]. This suggests that the crosslinker, rather than the collagen concentration, governs stiffness in this system.",
            "Cell viability exceeded 90% on every scaffold (Figure 4), in line with the low cytotoxicity of the crosslinker reported by others [22]; the higher cell number on the stiffer scaffolds is consistent with mechanosensitive proliferation.",
        ],
        "discussion": [
            "Our findings demonstrate that the crosslinked scaffolds support cell growth while providing mechanical properties in the range of native cartilage. This is consistent with previous reports [24,25], although the earlier studies used higher crosslinker concentrations.",
            "Several limitations should be acknowledged. The sample size was small, follow-up was limited to 12 weeks, and the in vitro model does not capture the inflammatory environment of the injured joint. Further studies in large animal models are warranted.",
            "In conclusion, we have developed a simple and scalable method for stiffening collagen hydrogels. These results suggest that the scaffolds may be a promising candidate for clinical translation.",
        ],
        "references": _ENTRIES[:4],
        "back": [
            "The authors thank Dr J. Doe for technical assistance with electron microscopy. This work was supported by grant 123456 from the National Science Foundation.",
            "Author contributions: J.S. and M.G. designed the study; W.Z. performed the experiments; all authors wrote the manuscript. Competing interests: The authors declare no competing interests.",
            "Supplementary data to this article can be found online at https://doi.org/10.1016/j.example.2021.100001.",
        ],
    }

KINDS: dict[str, Kind] = {
    "heading": Kind("heading", HEADING_PROTOTYPES, threshold=0.75, margin=0.08, prefix=QUERY),
    "front": Kind(
        "front",
        {
            "authors": [
                "John A. Smith, Maria García-López, Wei Zhang and Priya Natarajan",
                "A. B. Jones1, C. D. Lee2,*, E. F. Brown1,2",
                "Hannah Müller · Tomasz Nowak · Yuki Tanaka",
                "Robert K. Patel1,†, Sofia Rossi2,†, Daniel O'Connor1,3,*",
            ],
            "affiliations": [
                "Department of Biomedical Engineering, University of Toronto, Toronto, ON M5S 3G9, Canada",
                "1 Institute of Materials Science, Chinese Academy of Sciences, Beijing 100190, China; 2 School of Medicine, Stanford University, Stanford, CA 94305, USA",
                "Laboratory of Tissue Engineering, Hospital Clínic de Barcelona, 08036 Barcelona, Spain",
                "Faculty of Pharmacy, Cairo University, Kasr El-Aini Street, Cairo 11562, Egypt",
            ],
            "dates": [
                "Received 12 March 2021; Revised 4 June 2021; Accepted 9 June 2021; Available online 15 June 2021",
                "Received: 3 January 2020 / Accepted: 28 February 2020 / Published online: 10 March 2020",
                "Article history: Received 2 May 2019, Accepted 30 July 2019",
                "First published: 14 October 2022",
            ],
            "correspondence": [
                "* Corresponding author. E-mail address: jsmith@univ.edu (J. Smith).",
                "Correspondence to: Dr Maria García, Department of Chemistry; Tel.: +34 93 402 1234; Fax: +34 93 402 1233",
                "Address correspondence to Wei Zhang, wzhang@example.ac.cn",
                "† These authors contributed equally to this work. ORCID: 0000-0002-1825-0097",
            ],
            "keywords": [
                "Keywords: collagen; hydrogel; tissue engineering; crosslinking; mechanical properties",
                "Key words: osteoarthritis, cartilage, biomarkers, MRI",
                "Index Terms—deep learning, segmentation, medical imaging",
            ],
            "funding": [
                "This work was supported by the National Natural Science Foundation of China (grant nos. 51873120 and 82072396) and the Fundamental Research Funds for the Central Universities.",
                "Funding: The research leading to these results received funding from the European Research Council under grant agreement no. 648102.",
                "This study was funded by NIH grant R01 AR068426 to J.S. and a fellowship from the Wellcome Trust.",
            ],
            "notice": [
                "ORIGINAL RESEARCH ARTICLE",
                "Journal of Materials Chemistry B",
                "Research Article",
                "Contents lists available at ScienceDirect",
                "Cite this: RSC Adv., 2021, 11, 3025",
                "REVIEW",
                "Frontiers in Bioengineering and Biotechnology | www.frontiersin.org",
                "HHS Public Access Author manuscript",
                "© 2021 The Authors. Published by Elsevier Ltd. This is an open access article under the CC BY license (http://creativecommons.org/licenses/by/4.0/).",
                "This article is licensed under a Creative Commons Attribution 4.0 International License, which permits use, sharing, adaptation, distribution and reproduction in any medium or format.",
                "Copyright © 2019 American Chemical Society. All rights reserved.",
            ],
            "prose": _PROSE_STARTS,
        },
        threshold=0.78,  # measured on 900 typed lines of the corpora: at 0.78/0.05 about one line in a hundred is misnamed, a third are named
        margin=0.05,
    ),
    "label": Kind(
        "label",
        {
            "label": [
                "Keywords", "Acknowledgements", "Author contributions", "Funding", "Data availability", "Conflicts of interest", "Ethics approval", "Supplementary material", "Correspondence", "Abbreviations", "Background", "Methods", "Results", "Conclusions", "Objectives", "Study design", "Level of evidence", "Clinical relevance", "Highlights", "Significance", "Declarations", "Appendix", "Notes", "Footnotes", "Associated data", "Peer review information", "Publisher's note", "Competing interests", "Availability of data and materials", "Consent for publication", "Received",
            ],
            "prose": ["The samples were", "In this study we", "Figure 3 shows", "These results suggest", "Cells were cultured", "However, the", "A total of 24", "After 7 days", "Statistical significance was", "Previous studies have", "Our group recently", "Two of the"],
        },
        threshold=0.85,
        margin=0.08,
        prefix=QUERY,
    ),
    "figtext": Kind(
        "figtext",
        {
            "caption": [
                "Figure 1. Schematic illustration of the fabrication process of the composite hydrogel scaffolds.",
                "Fig. 2 Representative SEM images of the scaffolds at (a) low and (b) high magnification. Scale bars: 100 µm.",
                "Figure 3. Cell viability after 1, 3 and 7 days of culture (n = 3, mean ± SD; *p < 0.05).",
                "Scheme 1 Synthesis route of the crosslinker.",
                "Fig. 4. Western blot analysis of collagen I expression in the three groups.",
                "Figure 5 | Mechanical properties of the hydrogels. (a) Stress–strain curves; (b) compressive modulus.",
            ],
            "prose": _PROSE_STARTS,
            "junk": ["0 20 40 60 80 100", "Time (h)", "Relative expression (fold change)", "Control PBS Treated", "a b c d", "Stress (MPa) Strain (%)", "Day 1 Day 3 Day 7", "* ** ns", "100 µm", "Intensity (a.u.) Wavenumber (cm-1)", "Group A Group B Group C"],
        },
        threshold=0.70,
        margin=0.05,
    ),
    "refentry": Kind("refentry", {"entry": _ENTRIES, "prose": _PROSE_STARTS}, threshold=0.70, margin=0.05),
    # what kind of paper this is, from the label a publisher prints above the title
    "type-label": Kind(
        "type-label",
        {
            "research": ["ORIGINAL RESEARCH", "Research Article", "Original Article", "Full Paper", "Article", "Original Research Article", "Regular Article", "Full Length Article"],
            "review": ["Review", "Review Article", "Mini Review", "Systematic Review", "Critical Review", "Topical Review", "Progress Report", "Feature Article"],
            "letter": ["Letter to the Editor", "Correspondence", "Letter", "Reply", "Short Communication"],
            "editorial": ["Editorial", "Commentary", "Opinion", "Perspective", "Viewpoint", "News and Views", "Comment"],
            "case-report": ["Case Report", "Case Series", "Clinical Case Report", "Case Study"],
            "protocol": ["Protocol", "Study Protocol", "Methods Article", "Technical Note"],
            "data": ["Data Descriptor", "Data in Brief", "Data Article", "Data Note"],
            "correction": ["Correction", "Erratum", "Corrigendum", "Retraction", "Author Correction"],
        },
        threshold=0.80,
        margin=0.08,
        prefix=QUERY,
    ),
    # what kind of paper this is, from its shape: title, the abstract's first sentences, its headings in order
    "profile": Kind(
        "profile",
        {
            "research": [
                "Title: Bioglass incorporation improves mechanical properties of electrochemically aligned collagen threads. Abstract: We report a crosslinked scaffold and measure its stiffness and cell viability in vitro. The modulus rose with crosslinker content while porosity was preserved. Sections: 1. Introduction; 2. Materials and Methods; 3. Results; 4. Discussion; 5. Conclusions; Acknowledgments; References",
                "Title: Effects of substrate stiffness on the tenogenic differentiation of mesenchymal stem cells. Abstract: Human MSCs were cultured on hydrogels of three stiffnesses for 14 days. Tenogenic markers rose on the stiffest substrate. Sections: Introduction; Experimental; Results and discussion; Conclusions; Conflicts of interest; References",
                "Title: Prevalence of resistance mutations in Mycobacterium tuberculosis isolates from a national survey. Abstract: We sequenced 339 isolates and tested them for resistance. Sections: ABSTRACT; INTRODUCTION; MATERIALS AND METHODS; RESULTS; DISCUSSION; ACKNOWLEDGMENTS; REFERENCES",
            ],
            "review": [
                "Title: Cellulose-based hybrid hydrogels for tissue engineering applications: a sustainable approach. Abstract: This review surveys the sources of cellulose, its derivatives and the hydrogels made from them, and their applications in tissue engineering. Sections: 1. Introduction; 2. Cellulose Sources and Derivatives; 3. Synthesis of Cellulose-Based Hydrogels; 4. Stimuli-Responsive Hydrogels; 5. Applications in Tissue Engineering; 6. Conclusions and Future Perspectives; References",
                "Title: Advances in oriented collagen fibrils for biomedical applications. Abstract: We summarise recent progress in aligning collagen and discuss the challenges that remain. Sections: 1 Introduction; 2 Structure of Collagen; 3 Alignment Methods; 4 Characterisation; 5 Biomedical Applications; 6 Challenges and Outlook; References",
            ],
            "letter": [
                "Title: Comment on the reported modulus of crosslinked collagen threads. Abstract: (none) Sections: (no headings)",
                "Title: Re: Outcomes after tendon repair with augmented sutures. Abstract: (none) Sections: Dear Editor; References",
            ],
            "editorial": [
                "Title: The next decade of tissue engineering: a perspective. Abstract: (none) Sections: Introduction; Where the field stands; What must change; References",
                "Title: Editorial: Special issue on biomaterials for regeneration. Abstract: (none) Sections: (no headings)",
            ],
            "case-report": [
                "Title: Mandibular reconstruction with a patient-specific implant after segmental resection: a case report. Abstract: A 54-year-old man presented with a tumour of the mandible. We describe the reconstruction and the outcome at two years. Sections: Introduction; Case Presentation; Discussion; Conclusion; References",
                "Title: Delayed rupture after collagen sling implantation: a case series. Abstract: We report three patients. Sections: Introduction; Case 1; Case 2; Case 3; Discussion; References",
            ],
            "protocol": [
                "Title: Effect of early mobilisation after flexor tendon repair: study protocol for a randomised controlled trial. Abstract: This protocol describes a two-arm trial of 120 patients. Sections: Background; Methods/Design; Discussion; Trial status; References",
            ],
            "data": [
                "Title: Dataset of mechanical tests on electrochemically aligned collagen threads. Abstract: This data article presents raw stress-strain records for 60 threads. Sections: Specifications Table; Value of the Data; Data Description; Experimental Design, Materials and Methods; Ethics Statement; References",
            ],
            "correction": [
                "Title: Correction to: Bioglass incorporation improves mechanical properties of collagen threads. Abstract: (none) Sections: (no headings)",
                "Title: Erratum: Effects of substrate stiffness on tenogenic differentiation. Abstract: (none) Sections: (no headings)",
            ],
        },
        threshold=0.70,
        margin=0.04,
    ),
}


DATA = Path(__file__).parent / "data"


def block_kind() -> Kind:
    """The block kind: seven lane centroids and a position prior computed once from every
    labelled paragraph of six libraries (`data/block_lanes.json`, no text in it; made by
    `python -m litrag_parser.meaning --make-centroids`), which separate lanes far better
    than a handful of example paragraphs — measured library-out, a section's mean over three
    or more paragraphs names methods, results or references wrongly about four times in a
    hundred at a margin of 0.08 (NOTES.md). Without the data file the kind is silent: the
    example paragraphs were near chance and decide nothing."""
    path = DATA / "block_lanes.json"
    if not path.exists():
        return Kind("block", {}, threshold=0.5, margin=0.08, source="no data/block_lanes.json: silent")
    data = json.loads(path.read_text("utf-8"))
    return Kind("block", {}, threshold=0.5, margin=0.08, prefix=data.get("prefix", CLASSIFY), centroids=data["centroids"], position=data.get("position"), source=data.get("from", ""), embedder=data.get("model"))


KINDS["block"] = block_kind()


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) + 1e-9)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def key_of(text: str) -> str:
    """A short text is its own key; a long one is keyed by its hash."""
    t = _squash(text)
    return t if len(t) <= 200 else "sha1:" + hashlib.sha1(t.encode("utf-8")).hexdigest()


def _rank(vec: list[float], protos: list[tuple[str, list[float]]]) -> list[tuple[str, float]]:
    best: dict[str, float] = {}
    for group, pv in protos:
        c = _cos(vec, pv)
        if c > best.get(group, -1.0):
            best[group] = c
    return sorted(best.items(), key=lambda x: (-x[1], x[0]))


def _decide(ranked: list[tuple[str, float]], threshold: float, margin: float, lane_margin: dict[str, float] | None = None) -> Verdict:
    name, score = ranked[0]
    gap = score - ranked[1][1] if len(ranked) > 1 else 1.0
    need = max(margin, (lane_margin or {}).get(name, 0.0))
    return Verdict(name if score >= threshold and gap >= need else "other", round(score, 4), round(gap, 4))


class Oracle:
    """Every kind's verdicts, from the store first and the embedder only for a text no one has asked about."""

    def __init__(self, cache: Path | None, url: str | None = None, model: str = DEFAULT_MODEL):
        self.url = (url or os.environ.get("LITRAG_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
        self.model = model
        self.cache_path = cache
        self.kinds: dict[str, Kind] = {}
        self._memo: dict[tuple[str, str], Verdict] = {}
        self._protos: dict[str, list[tuple[str, list[float]]]] = {}
        self._down = False
        self._down_at = 0.0
        self.error: str | None = None
        self._lock = threading.Lock()
        self.asked: dict[str, int] = {}
        self.named: dict[str, int] = {}
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(cache), check_same_thread=False, timeout=30)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("CREATE TABLE IF NOT EXISTS verdicts (kind TEXT NOT NULL, key TEXT NOT NULL, model TEXT NOT NULL, name TEXT NOT NULL, score REAL, margin REAL, at TEXT, PRIMARY KEY (kind, key, model))")
            self._conn.commit()
        else:
            self._conn = None

    def register(self, kind: Kind) -> None:
        if kind.centroids is not None and kind.embedder != self.model:
            # centroids from another embedder are not in this one's space: the kind falls silent
            kind = Kind(kind.name, {}, kind.threshold, kind.margin, kind.prefix, source=f"centroids made with {kind.embedder}, not {self.model}: silent")
        self.kinds[kind.name] = kind
        if kind.name == "heading":
            self._migrate_lanes(kind)

    def _migrate_lanes(self, kind: Kind) -> None:
        """The rows `lanes.py` wrote before there was a `verdicts` table, copied once: the
        old table is renamed afterwards, so the copy never runs again."""
        if self._conn is None:
            return
        if not self._conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'lanes'").fetchone():
            return
        self._conn.execute("INSERT OR IGNORE INTO verdicts (kind, key, model, name, score, margin, at) SELECT 'heading', heading, ?, lane, score, margin, at FROM lanes WHERE model = ?", (self._model_of(kind), self.model))
        self._conn.execute("ALTER TABLE lanes RENAME TO lanes_migrated")
        self._conn.commit()

    # -- the embedder ---------------------------------------------------------------------------
    BATCH = 64  # texts per request: a reference list of three hundred entries goes in five
    RETRY_AFTER = 60.0  # seconds before an embedder that failed is tried again

    def _embed(self, texts: list[str]) -> list[list[float]] | None:
        if self._down and time.monotonic() - self._down_at < self.RETRY_AFTER:
            return None
        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), self.BATCH):
                req = urllib.request.Request(f"{self.url}/api/embed", data=json.dumps({"model": self.model, "input": texts[i : i + self.BATCH]}).encode("utf-8"), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    out.extend(json.loads(r.read().decode("utf-8"))["embeddings"])
            if len(out) != len(texts):
                raise ValueError(f"{len(out)} embeddings for {len(texts)} texts")
            self._down = False
            return out
        except Exception as e:  # noqa: BLE001 - whatever went wrong, the answer is the same: no embedder for now
            self._down = True  # every unknown text stays `other`, and nothing is stored, until the next try
            self._down_at = time.monotonic()
            self.error = f"{type(e).__name__}: {e}"[:200]
            return None

    def _prototypes(self, kind: Kind) -> list[tuple[str, list[float]]] | None:
        if kind.silent:
            return None
        if kind.centroids is not None:
            return [(group, vec) for group, vec in kind.centroids.items()]
        if kind.name not in self._protos:
            flat = [(group, p) for group, ps in kind.groups.items() for p in ps]
            vecs = self._embed([kind.prefix + p for _, p in flat])
            if vecs is None:
                return None
            self._protos[kind.name] = [(group, v) for (group, _), v in zip(flat, vecs)]
        return self._protos[kind.name]

    def _model_of(self, kind: Kind) -> str:
        return f"{self.model}@{kind.signature()}"

    # -- the store ------------------------------------------------------------------------------
    def _lookup(self, kind: Kind, key: str) -> Verdict | None:
        memo = self._memo.get((kind.name, key))
        if memo is not None:
            return memo
        if self._conn is not None:
            row = self._conn.execute("SELECT name, score, margin FROM verdicts WHERE kind = ? AND key = ? AND model = ?", (kind.name, key, self._model_of(kind))).fetchone()
            if row:
                v = Verdict(row[0], row[1] or 0.0, row[2] or 0.0)
                self._memo[(kind.name, key)] = v
                return v
        return None

    def _save(self, kind: Kind, key: str, v: Verdict) -> None:
        self._memo[(kind.name, key)] = v
        if self._conn is not None:
            self._conn.execute("INSERT OR REPLACE INTO verdicts (kind, key, model, name, score, margin, at) VALUES (?, ?, ?, ?, ?, ?, ?)", (kind.name, key, self._model_of(kind), v.name, v.score, v.margin, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
            self._conn.commit()

    def _count(self, kind: str, verdicts: list[Verdict], asked: int) -> None:
        self.asked[kind] = self.asked.get(kind, 0) + asked
        self.named[kind] = self.named.get(kind, 0) + sum(1 for v in verdicts if v.sure)

    # -- the questions --------------------------------------------------------------------------
    def nearest(self, kind_name: str, text: str) -> Verdict:
        return self.nearest_many(kind_name, [text])[0]

    def nearest_many(self, kind_name: str, texts: list[str]) -> list[Verdict]:
        """One verdict per text, the store answering what it can and one embedder call for the rest."""
        kind = self.kinds[kind_name]
        with self._lock:
            keys = [key_of(t) for t in texts]
            out: list[Verdict | None] = [self._lookup(kind, k) for k in keys]
            missing = [i for i, v in enumerate(out) if v is None]
            answered = 0
            if missing:
                protos = self._prototypes(kind)
                vecs = self._embed([kind.prefix + _squash(texts[i]) for i in missing]) if protos is not None else None
                if vecs:
                    for i, vec in zip(missing, vecs):
                        v = _decide(_rank(vec, protos), kind.threshold, kind.margin, kind.lane_margin)
                        self._save(kind, keys[i], v)
                        out[i] = v
                        answered += 1
            verdicts = [v if v is not None else Verdict("other") for v in out]
            self._count(kind.name, verdicts, answered)
            return verdicts

    def scores(self, kind_name: str, texts: list[str], positions: list[float] | None = None) -> list[dict[str, float]] | None:
        """Every group's best cosine for every text (plus the kind's position prior, when it
        has one and `positions` — each text's place in the paper, 0 to 1 — are given), or
        None with no embedder. Not stored: the caller stores what it decides from them (a
        paper's run of lanes), keyed by the texts."""
        kind = self.kinds[kind_name]
        with self._lock:
            protos = self._prototypes(kind)
            vecs = self._embed([kind.prefix + _squash(t) for t in texts]) if protos is not None and texts else None
            if not vecs:
                return None
            self.asked[kind.name] = self.asked.get(kind.name, 0) + len(texts)
            out = []
            for i, vec in enumerate(vecs):
                at = positions[i] if positions is not None and i < len(positions) else None
                out.append({g: round(c + kind.prior(g, at), 6) for g, c in _rank(vec, protos)})
            return out

    def which(self, text: str, candidates: list[str], *, threshold: float | None = None, margin: float | None = None, kind_name: str = "which") -> Verdict:
        """Which candidate the text belongs with: its index as the name, or `other` when none is
        clearly nearest. Stored by the hash of the text, the candidates and the rule."""
        if not candidates:
            return Verdict("other")
        kind = self.kinds[kind_name]
        threshold = kind.threshold if threshold is None else threshold
        margin = kind.margin if margin is None else margin
        with self._lock:
            key = "sha1:" + hashlib.sha1("\n␞\n".join([f"{threshold}/{margin}", _squash(text), *[_squash(c) for c in candidates]]).encode("utf-8")).hexdigest()
            known = self._lookup(kind, key)
            if known is not None:
                self._count(kind.name, [known], 0)
                return known
            vecs = self._embed([kind.prefix + _squash(text), *[kind.prefix + _squash(c) for c in candidates]])
            if not vecs:
                return Verdict("other")
            ranked = _rank(vecs[0], [(str(i), v) for i, v in enumerate(vecs[1:])])
            v = _decide(ranked, threshold, margin)
            self._save(kind, key, v)
            self._count(kind.name, [v], 1)
            return v

    def remember(self, kind_name: str, key: str, v: Verdict) -> None:
        """A verdict the caller reached from `scores`, kept so a rebuild replays it."""
        kind = self.kinds[kind_name]
        with self._lock:
            self._save(kind, key, v)
            if v.sure:
                self.named[kind.name] = self.named.get(kind.name, 0) + 1

    def recall(self, kind_name: str, key: str) -> Verdict | None:
        kind = self.kinds[kind_name]
        with self._lock:
            return self._lookup(kind, key)

    def summary(self) -> dict[str, Any]:
        kinds = sorted(set(self.asked) | set(self.named))
        return {"model": self.model, "down": self._down, "error": self.error, "kinds": {k: {"asked": self.asked.get(k, 0), "named": self.named.get(k, 0)} for k in kinds}}


WHICH = Kind("which", {}, threshold=0.6, margin=0.05, prefix=CLASSIFY)  # no examples: the candidates are the groups

#: the heading kind with the two lanes the vocabulary otherwise names exactly — for the
#: experiment `LITRAG_VOCABULARY=off`, where the embedder must name every heading alone.
#: "Graphical abstract", "Highlights" and "Article history" lie near these, which is why
#: production keeps them to the vocabulary; the experiment measures what that costs.
HEADING_ALONE = Kind(
    "heading-alone",
    {
        **HEADING_PROTOTYPES,
        # the first run with the vocabulary off (NOTES.md, 2026-09-13) lost "Experimental" and its kin to
        # the margin against "Experimental results", and "Summary and outlook" to an abstract example
        # named "Summary": examples, not patterns, are how the embedder is told what a lane means
        "methods": [*HEADING_PROTOTYPES["methods"], "Experimental", "Experimental work", "Experimental design"],
        "discussion": [*HEADING_PROTOTYPES["discussion"], "Summary and outlook", "Perspectives", "Summary and perspectives"],
        "abstract": ["Abstract", "Summary", "Synopsis", "Structured abstract", "Lay summary"],
        "references": ["References", "Bibliography", "Works cited", "Notes and references", "References and notes"],
    },
    threshold=0.75,
    margin=0.08,
    prefix=QUERY,
    lane_margin={"abstract": 0.15, "references": 0.15},  # "Graphical abstract" and "Literature search" lie near these and are not them
)


def standard(cache: Path | None, url: str | None = None, model: str | None = None) -> Oracle:
    """An oracle with every kind in `KINDS` registered, and `which`."""
    o = Oracle(cache, url=url, model=model or os.environ.get("LITRAG_LANES_MODEL") or DEFAULT_MODEL)
    for kind in KINDS.values():
        o.register(kind)
    o.register(WHICH)
    o.register(HEADING_ALONE)
    return o


def make_centroids(libs: list[Path], out: Path, cap: int = 2500, url: str | None = None, model: str | None = None) -> dict[str, Any]:
    """`data/block_lanes.json` from the libraries' own rows: for every paragraph or list item
    whose lane a heading gave (roles in LANES, eight words or more), one unit vector per lane
    (the mean of the paragraphs' unit vectors, capped at `cap` per lane so the reference lists
    do not drown the rest) and, per lane, a histogram of where in the paper its paragraphs sit
    — the index among the paper's lane-labelled paragraphs in reading order, in tenths. No
    text leaves the store."""
    import random
    import sqlite3

    from . import __version__

    lanes = ("introduction", "methods", "results", "results-discussion", "discussion", "references", "back")
    o = Oracle(None, url=url, model=model or os.environ.get("LITRAG_LANES_MODEL") or DEFAULT_MODEL)
    rows: list[tuple[str, float, str]] = []
    for lib in libs:
        conn = sqlite3.connect(Path(lib) / "store.sqlite")
        for (key,) in conn.execute("SELECT key FROM papers WHERE status = 'parsed'"):
            paras = [r for r in conn.execute("SELECT role, text FROM nodes WHERE paper = ? AND type IN ('paragraph', 'list_item') ORDER BY ordinal", (key,)) if r[0] in lanes and len(r[1].split()) >= 8]
            for i, (role, text) in enumerate(paras):
                rows.append((role, i / max(len(paras) - 1, 1), _squash(text)))
        conn.close()
    rng = random.Random(5)
    rng.shuffle(rows)
    kept: list[tuple[str, float, str]] = []
    count: dict[str, int] = {}
    for r in rows:
        if count.get(r[0], 0) < cap:
            kept.append(r)
            count[r[0]] = count.get(r[0], 0) + 1
    vecs = o._embed([CLASSIFY + t for _, _, t in kept])
    if vecs is None:
        raise RuntimeError(f"no embedder: {o.error}")
    dim = len(vecs[0])
    centroids: dict[str, list[float]] = {}
    for lane in lanes:
        acc = [0.0] * dim
        for (role, _, _), v in zip(kept, vecs):
            if role == lane:
                norm = math.sqrt(sum(x * x for x in v)) + 1e-9
                for d in range(dim):
                    acc[d] += v[d] / norm
        norm = math.sqrt(sum(x * x for x in acc)) + 1e-9
        centroids[lane] = [round(x / norm, 6) for x in acc]
    position = {lane: [1.0] * 10 for lane in lanes}
    for role, at, _ in kept:
        position[role][min(int(at * 10), 9)] += 1
    for lane in lanes:
        total = sum(position[lane])
        position[lane] = [round(x / total, 5) for x in position[lane]]
    data = {
        "model": o.model,
        "prefix": CLASSIFY,
        "made": time.strftime("%Y-%m-%d"),
        "parser": __version__,
        "libraries": [Path(lib).name for lib in libs],
        "from": f"every paragraph or list item whose lane a heading gave, eight words or more, of {len(libs)} libraries, capped at {cap} per lane: one unit vector per lane and a histogram (tenths) of the paragraph's index among the paper's lane-labelled paragraphs in reading order; no text",
        "counts": count,
        "centroids": centroids,
        "position": position,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")), "utf-8")
    return data


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="litrag_parser.meaning", description=__doc__.split("\n\n")[0])
    ap.add_argument("--make-centroids", action="store_true", help="write data/block_lanes.json from the libraries' labelled paragraphs")
    ap.add_argument("--lib", action="append", default=[], help="a library directory (repeatable)")
    ap.add_argument("--out", default=str(DATA / "block_lanes.json"))
    ap.add_argument("--cap", type=int, default=2500)
    args = ap.parse_args(argv)
    if args.make_centroids and args.lib:
        data = make_centroids([Path(l).expanduser() for l in args.lib], Path(args.out), args.cap)
        print(f"wrote {args.out}: {data['counts']} · {data['model']}")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
