"""Headings canonicalised: every section's heading kept as written, and beside it the one name
the catalogue gives that kind of section — "Materials and methods" for "2. Experimental", "Conflicts
of interest" for "Declaration of competing interest" — so that retrieval, the audit and the reader's
own built headings all speak one vocabulary.

Three sources, rules first:

1. **The catalogue** (`CANON`): the canonical names, each with its lane and the spellings the XML
   libraries were found to use (`--harvest` counts them), and a few patterns for the families
   ("Conclusions and outlook", "Discussion and implications"). Exact, free, certain.
2. **The embedder**, where the catalogue is silent: centroids learned from every heading the XML
   libraries labelled — the lane a `sec-type` or the vocabulary gave a top-level heading, the
   parent's lane for a subsection, the catalogue's name where it matched — one unit vector per
   lane and per canonical name (`data/headings.json`, numbers only; `--make-centroids`). A name
   or a lane is taken only when near enough and clearly nearer than the next, measured
   library-out before it decides (`--measure`), else nothing: a heading with no canonical name is
   honest.
3. **Built headings** take the catalogue's name for their lane, which is the corpus's modal
   spelling — "Materials and methods", not "Methods".

    uv run --project parser python -m litrag_parser.headings --harvest --lib …          (heading, lane, canonical) counts from the XML libraries' sections, to <root>/headings.json
    uv run --project parser python -m litrag_parser.headings --make-centroids --lib …   data/headings.json from the harvest: lane and canonical-name centroids
    uv run --project parser python -m litrag_parser.headings --measure --lib …          library-out precision of the centroids against the vocabulary's and the catalogue's word
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .facets import normalise, role_of

LANES = ("abstract", "introduction", "methods", "results", "results-discussion", "discussion", "references", "back", "other")

#: canonical name → (lane, spellings as `_key` gives them). The spellings are what 514 XML files
#: of six libraries printed (NOTES.md, 2026-09-14), plus the obvious variants.
CANON: dict[str, tuple[str, tuple[str, ...]]] = {
    "Abstract": ("abstract", ("abstract", "structured abstract", "synopsis", "lay abstract", "abstracts")),
    "Graphical abstract": ("back", ("graphical abstract", "toc graphic", "table of contents graphic", "toc graphics")),
    "Highlights": ("other", ("highlights", "key points", "key messages", "significance statement", "summary points", "lay summary", "plain language summary", "author summary", "significance", "research in context", "what this paper adds", "key findings", "impact statement", "statement of significance", "novelty statement", "key message", "main points", "highlight", "what is already known", "what this study adds", "article highlights", "practitioner points", "new and noteworthy")),
    "Keywords": ("other", ("keywords", "key words", "index terms", "keyword", "key word", "mesh terms", "subject terms")),
    "Introduction": ("introduction", ("introduction", "background", "background and objectives", "background and aims", "objectives", "objective", "aims", "aim", "purpose", "motivation", "main", "main text", "overview", "scope", "rationale", "aims and objectives", "background and rationale", "study objectives", "general introduction", "preliminaries", "problem statement", "introduction and background", "background and introduction", "introduction and objectives", "aim of the study", "aims of the study", "purpose of the study", "objectives of the study", "context", "state of the art", "related work", "literature review", "background and significance", "hypothesis", "hypotheses", "research questions", "study aims")),
    "Materials and methods": ("methods", ("materials and methods", "materials methods", "methods", "method", "methodology", "experimental", "experimental section", "experimental procedures", "experimental methods", "experimental details", "experimental part", "methods and materials", "material and methods", "material and method", "materials and method", "patients and methods", "subjects and methods", "participants and methods", "study design and methods", "research design and methods", "experimental design", "theory and methods", "computational methods", "model and methods", "design and methods", "methods summary", "star methods", "online methods", "procedures", "research methods", "research methodology", "materials and experimental methods", "experimental and computational methods", "theoretical and experimental methods", "methods and data", "data and methods", "materials and experimental procedures", "experimental design and methods", "experimental setup", "experiments", "experimental procedure", "experimental methodology", "materials and methodology", "methods and procedures", "methods and analysis", "methods and materials", "methodological approach", "approach", "materials methods and analysis", "patients materials and methods", "animals materials and methods", "experimental work", "experimental protocol", "protocol", "materials and methods section", "methodology and data", "model", "models", "theory", "theoretical background", "theoretical framework", "framework", "system and methods", "system model", "problem formulation", "proposed method", "proposed methodology", "proposed approach", "the proposed method", "implementation", "computational details", "simulation details", "simulation methods", "numerical methods", "numerical method")),
    "Study design": ("methods", ("study design", "design", "study setting", "setting", "study population", "participants", "subjects", "patients", "population", "sample", "recruitment", "eligibility", "eligibility criteria", "inclusion and exclusion criteria", "inclusion criteria", "exclusion criteria", "study participants", "patient selection", "sampling", "study area", "study site", "study sites", "settings", "study design and participants", "study design and setting", "participants and setting", "population and sample", "sample size", "sample size calculation", "randomisation", "randomization", "trial design", "study period", "study population and design", "study design and population", "patients and study design", "design and setting", "setting and participants", "participants and procedures", "study cohort", "cohort", "patient population", "subjects and design", "study subjects", "animals and study design", "study design and sample", "selection of participants", "sampling and recruitment", "recruitment and participants", "study setting and population", "setting and design", "study design and procedures", "overview of the study design")),
    "Materials": ("methods", ("materials", "chemicals", "reagents", "reagents and materials", "chemicals and materials", "materials and reagents", "chemicals and reagents", "animals", "cell lines", "cells", "samples", "sample collection", "sample preparation", "specimens", "data sources", "data source", "data collection", "data", "dataset", "datasets", "data set", "instrumentation", "instruments", "apparatus", "equipment", "materials and chemicals", "chemicals and instruments", "reagents and chemicals", "cell culture", "cell lines and culture", "animal model", "animal models", "experimental animals", "plant material", "plant materials", "bacterial strains", "strains and culture conditions", "strains and growth conditions", "study material", "study materials", "material", "raw materials", "sources of data", "data acquisition", "data collection and processing", "data collection procedures", "data collection procedure", "data collection and management", "measurements", "instruments and measures", "measures", "outcome measures", "outcome measure", "variables", "study variables", "outcomes and measures", "variables and measurements", "assessments", "clinical assessment", "questionnaire", "questionnaires", "survey instrument", "the survey", "data extraction", "search strategy", "literature search", "search strategy and selection criteria", "information sources", "sources of information", "data sources and search strategy", "study selection", "selection criteria", "quality assessment", "risk of bias assessment")),
    "Statistical analysis": ("methods", ("statistical analysis", "statistics", "statistical methods", "data analysis", "statistical analyses", "data analysis and statistics", "statistical analysis and data", "statistical treatment", "statistical evaluation", "statistical considerations", "statistical procedures", "statistical approach", "statistical tests", "statistical analysis plan", "statistical method", "statistical analyses and data", "data analyses", "analysis plan", "statistical analysis and sample size", "statistical data analysis", "statistical modelling", "statistical modeling", "data management and analysis", "data management and statistical analysis", "data handling and statistics", "statistical analysis and data processing", "statistical evaluation of data", "statistical design", "statistical analysis and reporting", "statistical power", "power analysis", "power calculation")),
    "Results": ("results", ("results", "findings", "experimental results", "main results", "results summary", "observations", "observation", "main findings", "key results", "results overview", "summary of results", "empirical results", "simulation results", "numerical results", "results of the study", "study results", "principal findings", "result", "results section", "experimental findings", "observations and results", "results obtained", "the results", "results and findings", "findings of the study", "study findings", "results of the analysis", "analysis results", "experimental observations", "results and outcomes", "findings and results")),
    "Results and discussion": ("results-discussion", ("results and discussion", "results discussion", "findings and discussion", "results and discussions", "result and discussion", "results and analysis", "results analysis and discussion", "experimental results and discussion", "results and interpretation", "results and observations", "results and comments", "results with discussion", "findings and interpretation", "results and their discussion", "presentation and discussion of results", "results discussion and analysis", "results and discussion section", "data and discussion", "observations and discussion", "analysis and discussion", "outcomes and discussion", "experimental results and analysis")),
    "Discussion": ("discussion", ("discussion", "general discussion", "discussions", "comments", "comment", "discussion and conclusions", "discussion and conclusion", "discussion and implications", "discussion and perspectives", "interpretation", "discussion and future directions", "discussion and outlook", "discussion and summary", "discussion and limitations", "discussion and future work", "discussion of results", "summary and discussion", "discussion of the results", "discussion of findings", "discussion and recommendations", "discussion and future perspectives", "general discussion and conclusions", "discussion and conclusion remarks", "discussion and implications for practice", "discussion section", "discussion and clinical implications", "discussion and future research", "interpretation of results", "interpretation of the results", "discussion and analysis", "the discussion", "discussion of the findings")),
    "Conclusions": ("discussion", ("conclusion", "conclusions", "concluding remarks", "conclusions and outlook", "conclusions and future perspectives", "conclusions and perspectives", "conclusions and prospects", "summary and conclusions", "summary and conclusion", "conclusion and future work", "conclusions and future directions", "conclusions and future work", "outlook", "future perspectives", "future directions", "perspectives and conclusions", "summary and outlook", "closing remarks", "conclusion and outlook", "conclusion and perspectives", "conclusions and recommendations", "conclusion and recommendations", "conclusions and implications", "conclusion and future perspectives", "final remarks", "conclusions and future prospects", "conclusion and future directions", "perspectives", "perspective", "future work", "future outlook", "outlook and conclusions", "conclusions and outlooks", "summary and perspectives", "summary and future directions", "conclusions and summary", "recommendations", "recommendations and conclusions", "conclusion and future research", "conclusions and future research", "conclusion and perspective", "conclusions and future studies", "conclusion and summary", "conclusion remarks", "concluding remark", "final considerations", "final conclusions", "closing", "conclusions and future", "conclusions future perspectives", "conclusion and outlooks", "outlook and perspectives", "prospects", "future prospects", "future research", "future research directions", "future directions and conclusions", "conclusions and open questions", "open questions", "challenges and perspectives", "challenges and future perspectives", "challenges and outlook", "challenges and future directions", "current challenges and future perspectives", "summary and future perspectives", "summary and future outlook", "summary and prospects", "summary and future prospects", "summary and concluding remarks", "summary conclusions", "general conclusions", "general conclusion", "overall conclusions", "conclusion of the study", "conclusions of the study", "take home message", "take home messages", "lessons learned", "conclusions and clinical implications", "conclusions and future directions", "implications and conclusions", "conclusion and implications", "epilogue", "way forward", "the way forward", "next steps", "summary", "executive summary", "summary and outlook", "background summary")),
    "Limitations": ("discussion", ("limitations", "study limitations", "strengths and limitations", "limitations of the study", "limitations and future research", "limitations and future directions", "limitations and strengths", "strengths and weaknesses", "limitations of this study", "limitation", "limitations and future work", "limitations and perspectives", "limitations of the present study", "strengths and limitations of the study", "strengths and limitations of this study", "limitations and future studies", "limitations of the current study", "study strengths and limitations", "potential limitations", "limitations of the research", "caveats", "caveats and limitations", "strengths", "limitations and recommendations", "limitations and implications", "limitations and considerations", "assumptions and limitations")),
    "Implications": ("discussion", ("implications", "clinical implications", "implications for practice", "practical implications", "policy implications", "implications for research", "implications for clinical practice", "clinical relevance", "clinical significance", "translational relevance", "implications for policy and practice", "implications for future research", "clinical implications and future directions", "practice implications", "research implications", "theoretical implications", "managerial implications", "implications for practice and research", "implications and future directions", "implications and recommendations", "implications for policy", "public health implications", "practical applications", "applications", "potential applications", "clinical applications", "clinical application", "relevance", "translational implications", "significance of the findings")),
    "Case presentation": ("results", ("case presentation", "case report", "case description", "case history", "case summary", "case", "clinical case", "case reports", "patient presentation", "presentation of case", "case presentations", "cases", "case series", "clinical presentation", "the case", "case study", "case details", "patient information", "clinical findings", "case 1", "case 2", "case 3", "patient 1", "patient 2", "history and examination", "clinical course", "case report and discussion", "presentation", "report of a case", "report of case", "report of cases", "clinical history", "case illustration", "illustrative case", "case vignette", "case example", "patient and observation", "patient description", "clinical scenario", "index case", "our case")),
    "References": ("references", ("references", "reference", "literature cited", "bibliography", "works cited", "notes and references", "references and notes", "reference list", "uncited references", "citations", "literature", "sources", "references cited", "list of references", "cited literature", "further reading", "bibliographic references", "references and links", "references and recommended reading")),
    "Acknowledgements": ("back", ("acknowledgements", "acknowledgments", "acknowledgment", "acknowledgement", "thanks", "acknowledgements and funding", "acknowledgments and funding", "acknowledgement and funding", "acknowledgements and disclosures", "acknowledgments and disclosures", "acknowledgements and conflict of interest", "acknowledgement of funding", "acknowledgements and declarations", "acknowledgments and declarations", "acknowledgements and financial support", "acknowledgement and disclosure", "acknowledgements of support", "acknowledgements and author contributions")),
    "Author contributions": ("back", ("author contributions", "authors contributions", "author contribution", "credit authorship contribution statement", "credit author statement", "contributions", "contributors", "authorship", "author statement", "author contribution statement", "authors contribution", "contribution of authors", "contributorship", "authorship contribution", "credit authorship contribution", "authors contributions statement", "author roles", "author contributions statement", "authors roles", "roles of authors", "contribution statement", "authorship statement", "authorship contributions", "author s contributions", "credit roles", "contributor roles", "authorship and contributions", "individual contributions", "contributions of the authors", "contribution of the authors", "statement of authorship", "author involvement", "contributions to authorship")),
    "Funding": ("back", ("funding", "funding statement", "funding sources", "financial support", "funding information", "financial disclosure", "sources of funding", "grant support", "funding source", "financial support and sponsorship", "support", "sponsorship", "funders", "funding and support", "financing", "grants", "role of the funding source", "role of the funder", "funding declaration", "financial statement", "funding statements", "financial supports", "funding acknowledgement", "funding acknowledgements", "funding acknowledgments", "funding disclosure", "financial funding", "funding of the study", "study funding", "research funding", "financial support statement", "financial disclosure statement", "sources of support", "source of funding", "source of support", "funding and acknowledgements", "funding and acknowledgments", "grant information", "grant", "financial resources", "funding body", "funding bodies", "financial aid", "funding and conflicts of interest", "funding and disclosures", "role of funding source", "role of the sponsor", "role of funder")),
    "Conflicts of interest": ("back", ("conflicts of interest", "conflict of interest", "competing interests", "competing interest", "declaration of competing interest", "declaration of competing interests", "declaration of interests", "declaration of interest", "disclosure", "disclosures", "disclosure statement", "conflict of interest statement", "competing interest statement", "declaration of conflicting interests", "potential conflicts of interest", "financial interests", "declarations", "conflicts of interest statement", "conflict of interests", "disclosure of interest", "disclosure of interests", "competing financial interests", "declaration of conflict of interest", "competing interests statement", "financial disclosures", "declaration of conflicts of interest", "conflict of interest disclosure", "conflict of interest disclosures", "disclosure of potential conflicts of interest", "declarations of interest", "declaration of competing financial interests", "conflict of interest declaration", "conflicts of interest declaration", "declaration of conflicting interest", "competing interests declaration", "declaration", "disclosure of conflicts of interest", "disclosure of conflict of interest", "statement of competing interests", "statement of conflict of interest", "statement of conflicts of interest", "competing financial interest", "financial and competing interests disclosure", "conflicts", "no conflict of interest", "conflict of interest and funding", "funding and competing interests", "duality of interest", "financial interest", "financial and non financial interests", "declaration of financial interests", "author disclosure statement", "author disclosures", "authors disclosures", "author disclosure", "transparency declaration", "competing interest declaration", "financial competing interests")),
    "Data availability": ("back", ("data availability", "data availability statement", "availability of data and materials", "data sharing statement", "data access statement", "code availability", "data and code availability", "availability of data", "data statement", "accessibility of data", "data access", "research data", "availability of data and material", "data and materials availability", "data sharing", "data and software availability", "data availability and materials", "availability of supporting data", "data accessibility", "data availability statements", "code and data availability", "software availability", "materials availability", "data deposition", "accession numbers", "accession codes", "data access and sharing", "availability of data and code", "availability of data materials and code", "data and material availability", "data availability and reproducibility", "data sharing and data accessibility", "availability of the data", "data and resource availability", "resource availability", "data reporting", "data and code", "code and data", "reproducibility", "materials and data availability", "data and materials", "availability of materials and data", "data availability and code", "data sharing plans", "availability statement", "data deposit", "data archiving", "data archiving statement", "data repository", "supporting data")),
    "Ethics": ("back", ("ethics statement", "ethical approval", "ethics approval", "ethical considerations", "ethics approval and consent to participate", "institutional review board statement", "informed consent statement", "informed consent", "consent for publication", "patient consent statement", "ethical statement", "ethics", "ethics approval statement", "ethical approval and consent to participate", "consent", "patient consent", "ethical aspects", "ethics and consent", "ethical clearance", "ethics committee approval", "ethical approval statement", "human and animal rights", "animal ethics", "ethics declarations", "compliance with ethical standards", "ethical compliance", "research ethics", "statement of ethics", "ethical issues", "consent to participate", "consent statement", "ethics approval and informed consent", "animal welfare", "ethics and dissemination", "ethical approval and informed consent", "ethical approval and consent", "ethics and consent to participate", "institutional review board", "irb approval", "ethical permission", "ethical review", "ethics review", "ethical standards", "ethics statements", "ethical statements", "patient consent for publication", "informed consent and ethical approval", "consent to publish", "consent for publications", "animal care and use", "animal care", "animal ethics statement", "animal studies", "human subjects", "human participants", "human ethics", "protection of human subjects", "protection of human and animal subjects", "ethical guidelines", "ethical conduct", "ethical approvals", "ethics committee", "institutional review", "declaration of helsinki", "study approval")),
    "Supplementary material": ("back", ("supplementary material", "supplementary materials", "supporting information", "supplementary information", "supplemental information", "supplemental material", "supplemental materials", "electronic supplementary material", "supplementary data", "additional information", "additional files", "appendix", "appendices", "associated data", "source data", "extended data", "supplementary files", "online content", "reporting summary", "supplementary appendix", "supplementary figures", "supplementary tables", "supplementary figures and tables", "supplementary methods", "supplementary", "supplement", "supplements", "additional file", "appendix a", "appendix b", "appendix a supplementary data", "supplementary information statement", "supporting information available", "electronic supplementary information", "online supplementary material", "supplementary online material", "supplementary content", "additional supporting information", "supplementary material statement", "supplementary data statement", "supplementary material and methods", "supplementary note", "supplementary notes", "supplementary text", "supplementary file", "supplemental data", "supplemental digital content", "supplemental figures", "supplemental tables", "supplementary file 1", "additional data", "additional material", "additional materials", "additional resources", "online resources", "online resource", "electronic supplementary material esm", "esm", "annex", "annexes", "supplementary results", "supplementary discussion", "supplementary references", "supplementary figure legends", "supplementary tables and figures", "supporting material", "supporting materials", "supporting data", "supporting information s1", "appendix 1", "appendix 2", "appendix i", "appendix ii", "supplementary information available", "supporting information for", "the supporting information", "supplementary information appendix", "supplementary material available", "supplementary material s1", "extended data figures", "extended data tables", "data supplement", "web appendix", "web extra material", "multimedia appendix", "multimedia appendices", "supplementary video", "supplementary videos", "supplementary movies", "supplementary movie", "supplementary datasets", "supplementary dataset", "supplementary material online", "online supplementary information", "online supplemental material", "online supplement", "online appendix", "online material", "online resource 1")),
    "Abbreviations": ("back", ("abbreviations", "list of abbreviations", "glossary", "nomenclature", "abbreviations and acronyms", "list of symbols", "symbols", "abbreviation", "acronyms", "definitions", "glossary of terms", "list of abbreviations and acronyms", "notation", "abbreviations used", "nonstandard abbreviations", "abbreviation list", "abbreviations list", "nomenclature and abbreviations", "abbreviations and symbols", "symbols and abbreviations", "list of acronyms", "acronyms and abbreviations", "terminology", "definitions and abbreviations", "abbreviations and definitions", "notations", "list of notations", "glossary of abbreviations", "key to abbreviations", "abbreviations and nomenclature", "nomenclature and units", "units and abbreviations", "list of symbols and abbreviations", "abbreviations and terms", "terms and abbreviations", "terms and definitions", "definition of terms", "list of terms")),
    "Author information": ("back", ("author information", "authors information", "contributor information", "corresponding author", "correspondence", "biographical information", "about the authors", "biographies", "author details", "affiliations", "authors and affiliations", "author affiliations", "orcid", "author biographies", "biography", "about the author", "author notes", "author note", "authors notes", "author s note", "authors note", "corresponding authors", "correspondence to", "contact", "contact information", "author bios", "author biography", "authors biographies", "author profiles", "author profile", "biographical notes", "biographical sketch", "biographical sketches", "notes on contributors", "about the contributors", "author affiliation", "affiliation", "author identification", "orcid ids", "orcid id", "author orcids", "present address", "present addresses", "current address", "current addresses", "address", "addresses", "author addresses", "authors addresses", "contact details", "email", "e mail", "correspondence and requests for materials", "reprints and permissions", "reprint requests", "requests for reprints")),
    "Footnotes": ("back", ("footnotes", "notes", "endnotes", "publisher s note", "publisher note", "publishers note", "publisher disclaimer", "peer review", "peer review information", "peer review report", "transparency statement", "generative ai statement", "ai statement", "article information", "note", "disclaimer", "publisher s disclaimer", "editorial note", "editor s note", "provenance and peer review", "rights and permissions", "open access", "copyright", "additional declarations", "declaration of generative ai", "declaration of generative ai and ai assisted technologies in the writing process", "declaration of ai use", "use of artificial intelligence", "article history", "article info", "preprint", "preprints", "trial registration", "registration", "clinical trial registration", "prior presentation", "presented at", "meeting presentation", "guarantor", "patient and public involvement", "footnote", "end notes", "note added in proof", "notes added in proof", "publisher s notes", "editorial notes", "editor notes", "article note", "author disclaimer", "disclaimers", "additional notes", "peer review statement", "peer review history", "peer review status", "review history", "peer reviewer", "peer reviewers", "reviewer information", "reviewers", "handling editor", "handling editors", "editor", "editors", "academic editor", "associate editor", "reviewed by", "edited by", "editorial process", "editorial", "publication history", "history", "received", "accepted", "version of record", "version history", "version notes", "how to cite", "how to cite this article", "citation", "cite this article", "cite as", "citing this article", "article citation", "recommended citation", "reproducibility statement", "transparency", "transparency and openness", "open practices statement", "open science practices", "open science statement", "open data statement", "data transparency", "preregistration", "pre registration", "study registration", "registration and protocol", "protocol registration", "prospero registration", "trial registry", "clinical trial number", "trial registration number", "clinical trial registration number", "registration number", "keywords and abbreviations", "ai disclosure", "ai use disclosure", "use of ai", "use of ai tools", "artificial intelligence disclosure", "declaration of ai and ai assisted technologies", "statement on the use of ai", "use of generative ai", "language editing", "supporting agencies", "guarantor statement", "patient involvement", "public involvement", "patient and public involvement statement", "ppi statement", "presentation", "previous presentation", "previous publication", "prior publication", "related article", "related articles", "related content", "see also", "see related", "companion paper", "linked article", "linked articles", "graphical table of contents", "dedication", "in memoriam", "in memory of", "dedicated to", "epigraph", "motto", "quotation", "erratum", "correction", "corrections", "corrigendum", "addendum", "publisher correction", "author correction", "retraction", "retraction note", "expression of concern")),
}

#: the names that are a paper's own top-level sections; the rest (Study design, Materials, Statistical
#: analysis, Limitations, Implications) are subsections of one of them
TOP_LEVEL = frozenset(name for name in CANON if name not in ("Study design", "Materials", "Statistical analysis", "Limitations", "Implications"))

_KEY_LANE: dict[str, tuple[str, str]] = {}  # spelling key → (canonical name, lane)
for _name, (_lane, _spellings) in CANON.items():
    for _s in _spellings:
        _KEY_LANE.setdefault(_s, (_name, _lane))

#: the families, in order — the more specific first ("Results and discussion" before "Results")
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^results\b.*\bdiscussion\b|^findings\b.*\bdiscussion\b"), "Results and discussion"),
    (re.compile(r"^(?:conclusions?|concluding remarks?|summary and conclusions?|outlook|final remarks|closing remarks)\b|\bfuture (?:directions?|perspectives?|prospects?|research|work|outlook)\b|\bconclusions?$|\boutlook$|\bperspectives?$"), "Conclusions"),
    (re.compile(r"^(?:general )?discussion\b"), "Discussion"),
    (re.compile(r"\blimitations?\b"), "Limitations"),
    (re.compile(r"\bimplications?\b"), "Implications"),
    (re.compile(r"\bcase (?:presentation|report|description|history|summary|series|illustration)s?\b"), "Case presentation"),
    (re.compile(r"^(?:materials? and methods?|methods?|methodolog(?:y|ies)|experimental(?: section| procedures?| methods?| details?| part| setup)?|patients and methods|subjects and methods|materials methods)(?: and \w+)?$"), "Materials and methods"),
    (re.compile(r"\bstatistic|^data analys[ie]s\b"), "Statistical analysis"),
    (re.compile(r"\bdata collection\b|\bsample collection\b|\bsample preparation\b|\bdata sources?\b|\bmaterials? and (?:reagents|chemicals)\b|\bchemicals and (?:reagents|materials)\b"), "Materials"),
    (re.compile(r"\bstudy design\b|\bstudy population\b|\bparticipants\b|\beligibility\b|\binclusion (?:and exclusion )?criteria\b|\bsample size\b|\brandomi[sz]ation\b"), "Study design"),
    (re.compile(r"^results?\b|^findings\b|^main (?:results|findings)\b"), "Results"),
    (re.compile(r"\backnowledg"), "Acknowledgements"),
    (re.compile(r"\b(?:conflicts?|competing|duality) (?:of )?(?:financial )?interests?\b|\bdisclosures?\b|\bdeclarations? of (?:competing|conflicting|financial)"), "Conflicts of interest"),
    (re.compile(r"\bdata (?:availability|sharing|access(?:ibility)?|deposition)\b|\bavailability of (?:data|materials|code)\b|\bcode availability\b|\baccession (?:numbers?|codes?)\b"), "Data availability"),
    (re.compile(r"\bsupplementa(?:ry|l)\b|\bsupporting information\b|\bappendi(?:x|ces)\b|\bextended data\b|\bsource data\b|\badditional files?\b"), "Supplementary material"),
    (re.compile(r"\bethic|\binformed consent\b|\binstitutional review board\b|\bconsent (?:for|to) (?:publication|participate|publish)\b|\banimal (?:care|welfare)\b|\bhuman (?:subjects|participants)\b"), "Ethics"),
    (re.compile(r"\bauthors?(?:'|\ss)? contributions?\b|\bcredit\b.*\bauthorship\b|\bcontribut(?:ions|ors|orship)\b"), "Author contributions"),
    (re.compile(r"\bfunding\b|\bfinancial (?:support|disclosure)\b|\bgrant support\b|\bsources? of (?:funding|support)\b|\bsponsor"), "Funding"),
    (re.compile(r"\babbreviations?\b|\bglossary\b|\bnomenclature\b|\bacronyms\b|\blist of symbols\b"), "Abbreviations"),
    (re.compile(r"\bauthor information\b|\bcorresponding authors?\b|\bcorrespondence\b|\bbiograph|\baffiliations?\b|\borcid\b"), "Author information"),
    (re.compile(r"\bpeer review\b|\bpublisher'?s? (?:note|disclaimer)\b|\bfootnotes?\b|\bendnotes?\b|\bgenerative ai\b|\bartificial intelligence (?:statement|disclosure)\b|\btrial registration\b|\bhow to cite\b|\bcite this\b|\bregistration\b"), "Footnotes"),
    (re.compile(r"^(?:introduction|background|objectives?|aims?|purpose|motivation|rationale)\b|\bintroduction\b|\bobjectives?\b|\bhypothes[ie]s\b|\baims? of\b"), "Introduction"),
    (re.compile(r"^references?\b|\bliterature cited\b|\bbibliography\b|\bworks cited\b|\breference list\b"), "References"),
    (re.compile(r"\bkey ?words?\b|\bindex terms\b"), "Keywords"),
    (re.compile(r"\bhighlights?\b|\bkey (?:points|messages|findings)\b|\bsignificance statement\b|\blay summary\b|\bplain language summary\b"), "Highlights"),
    (re.compile(r"\bgraphical abstract\b|\btoc graphic\b"), "Graphical abstract"),
    (re.compile(r"^(?:abstract|summary|synopsis)\b"), "Abstract"),
]


def _key(heading: str) -> str:
    """"2.1. Materials & Methods:" → "materials methods": the vocabulary's normalisation, then
    letters, digits and single spaces only."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalise(heading)).split())


def canonical_of(heading: str | None, oracle: Any = None) -> tuple[str | None, str]:
    """The catalogue's name for a heading and how it was found — `table` (an exact spelling),
    `pattern` (a family), `meaning` (the centroids, when an oracle has the `canonical` kind and
    is sure) — or (None, "") when the heading names nothing the catalogue has."""
    key = _key(heading or "")
    if not key:
        return None, ""
    hit = _KEY_LANE.get(key)
    if hit is not None:
        return hit[0], "table"
    for pat, name in _PATTERNS:
        if pat.search(key):
            return name, "pattern"
    if oracle is not None and "canonical" in getattr(oracle, "kinds", {}):
        v = oracle.nearest("canonical", heading or "")
        if v.sure and v.name in CANON:
            return v.name, "meaning"
    return None, ""


#: the lanes a family may give a heading: a heading that ends in "limitations" or opens with "conclusions"
#: is discussion whatever else it says; references and back matter come from exact spellings only, since
#: "Reference materials", "Image registration" and "Contributions of macrophages" are none of them
_FAMILY_LANES = frozenset({"introduction", "methods", "results", "results-discussion", "discussion"})


def top_level_lane(heading: str | None, promote: bool = False) -> str | None:
    """The lane of a heading the catalogue names as a top-level section — by an exact spelling for
    any lane ("Declaration of Competing Interest" is back matter, "Case presentation" is results),
    by a family for the body lanes only — or None: the tree builder's rule where the vocabulary was
    silent. With `promote` (a heading's depth) only an exact spelling of two words or more counts:
    a single word the catalogue knows ("Notation", "Consent") is a subsection's name as often as a
    section's, and a family ("Results across models") is a subsection's."""
    name, how = canonical_of(heading)
    lane = lane_of_canonical(name)
    if lane in (None, "other", "abstract"):
        return None
    if promote:
        return lane if name in TOP_LEVEL and how == "table" and len(_key(heading or "").split()) >= 2 else None
    if how == "table" or (how == "pattern" and lane in _FAMILY_LANES):
        return lane
    return None


def agreed(name: str | None, role: str) -> str | None:
    """A canonical name only where its lane is the section's own, or has none: a "Summary" the
    reader filed as the abstract is not "Conclusions", and "Reference materials" under methods
    is not "References"."""
    if name is None:
        return None
    lane = lane_of_canonical(name)
    return name if lane in (role, "other") else None


def lane_of_canonical(name: str | None) -> str | None:
    """The lane the catalogue gives a canonical name; None for a name it does not have."""
    return CANON[name][0] if name in CANON else None


# -- the harvest: what the XML libraries' own sections are called, and what they are ------------------

SEC_TYPES = {
    "intro": "introduction", "introduction": "introduction", "background": "introduction",
    "materials|methods": "methods", "methods": "methods", "materials-and-methods": "methods", "materials and methods": "methods", "methods|materials": "methods", "materials": "methods", "subjects": "methods",
    "results": "results", "results|discussion": "results-discussion", "results|conclusions": "results-discussion",
    "discussion": "discussion", "conclusions": "discussion", "conclusion": "discussion", "discussion|conclusions": "discussion",
    "cases": "results", "case": "results",
    "ref-list": "references",
    "ack": "back", "fn-group": "back", "coi-statement": "back", "supplementary-material": "back", "data-availability": "back", "contrib-info": "back", "associated-data": "back", "glossary": "back", "app": "back", "bio": "back", "extended-data": "back", "floats-group": "back", "funding": "back", "ethics": "back", "author-contributions": "back", "notes": "back", "abbreviations": "back", "supplementary": "back", "data-access": "back", "availability": "back", "disclosure": "back", "financial-disclosure": "back", "conflict": "back",
    "abstract": "abstract",
}

_SEC = re.compile(r"<sec\b([^>]*)>|</sec>|<title>(.*?)</title>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def harvest(libs: list[Path]) -> list[dict[str, Any]]:
    """Every titled section of every JATS file in `libs`: its heading as written, its depth, the
    lane the file states (`sec-type`) or the vocabulary names (top-level, no oracle) or the
    parent gives (a subsection), the catalogue's name where an exact spelling or a family
    matched, the article type, the library. Nothing but headings leaves the files."""
    rows: list[dict[str, Any]] = []
    for lib in libs:
        for f in sorted(glob.glob(str(Path(lib) / "papers" / "*.xml"))):
            try:
                s = Path(f).read_text("utf-8", errors="replace")
            except OSError:
                continue
            at = re.search(r'<article\b[^>]*article-type="([^"]+)"', s[:60000])
            article_type = at.group(1) if at else None
            start, end = s.find("<body"), s.find("</body>")
            body = s[start:end] if start >= 0 and end > start else ""
            back = s[s.find("<back"):] if "<back" in s else ""
            for region, part in (("body", body), ("back", back)):
                stack: list[dict[str, Any]] = []
                pending: dict[str, Any] | None = None
                for m in _SEC.finditer(part):
                    tok = m.group(0)
                    if tok.startswith("</sec"):
                        if stack:
                            stack.pop()
                        pending = None
                        continue
                    if tok.startswith("<sec"):
                        st = re.search(r'sec-type="([^"]+)"', m.group(1) or "")
                        pending = {"depth": len(stack) + 1, "sec_type": st.group(1).lower() if st else None}
                        stack.append(pending)
                        continue
                    if pending is None:
                        continue  # a title that is not a section's (a figure's, a table's)
                    title = " ".join(_TAG.sub(" ", m.group(2) or "").split())
                    sec = pending
                    pending = None
                    if not title or len(title.split()) > 12:
                        continue
                    lane = None
                    stated = False
                    if sec["depth"] == 1:
                        lane = role_of(title, meaning=False)  # the vocabulary's exact word first: JATS marks a combined "Results and discussion" as `results`
                        if lane == "other":
                            lane = SEC_TYPES.get(sec["sec_type"] or "")
                            stated = lane is not None
                        if lane is None and region == "back":
                            lane = "back"
                    if lane is None and sec["depth"] > 1:
                        parent = next((x.get("lane") for x in reversed(stack[:-1]) if x.get("lane")), None)
                        lane = parent
                    sec["lane"] = lane
                    name, how = canonical_of(title)
                    rows.append({"heading": title, "depth": sec["depth"], "lane": lane, "stated": stated, "canonical": name, "how": how, "article_type": article_type, "library": Path(lib).name, "region": region})
    return rows


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) + 1e-9
    return [x / n for x in v]


def _mean_unit(vecs: list[list[float]]) -> list[float] | None:
    if not vecs:
        return None
    dim = len(vecs[0])
    acc = [0.0] * dim
    for v in vecs:
        u = _unit(v)
        for d in range(dim):
            acc[d] += u[d]
    return [round(x, 5) for x in _unit(acc)]


def _embed_unique(headings: list[str], url: str | None, model: str | None) -> tuple[dict[str, list[float]], str]:
    from .meaning import DEFAULT_MODEL, QUERY, Oracle, _squash

    o = Oracle(None, url=url, model=model or os.environ.get("LITRAG_LANES_MODEL") or DEFAULT_MODEL)
    uniq = sorted(set(headings))
    vecs = o._embed([QUERY + _squash(h) for h in uniq])
    if vecs is None:
        raise RuntimeError(f"no embedder: {o.error}")
    return dict(zip(uniq, vecs)), o.model


#: the reader's own sections and the front matter's lines: never a body heading the embedder must name
NO_CENTROID = frozenset({"Abstract", "Keywords", "Graphical abstract", "Highlights"})


def centroids_from(rows: list[dict[str, Any]], vectors: dict[str, list[float]], exclude_library: str | None = None) -> tuple[dict[str, list[float]], dict[str, list[float]], dict[str, int], dict[str, int]]:
    """One centroid per canonical name, from every heading the catalogue matched exactly or by
    family (each distinct spelling counted at most three times), and the lane prototypes as those
    same centroids grouped by lane — `discussion/Discussion`, `discussion/Conclusions`,
    `back/Funding` — plus one `<lane>/rest` centroid per lane from the top-level headings a
    file's `sec-type` laned but the catalogue did not name. A lane with one blended centroid
    put "Discussion" itself nearer "Results and discussion" than its own lane (NOTES.md);
    several per lane, the best counting, do not. Leaves one library out when asked."""
    name_vecs: dict[str, list[list[float]]] = defaultdict(list)
    rest_vecs: dict[str, list[list[float]]] = defaultdict(list)
    seen_name: Counter = Counter()
    seen_rest: Counter = Counter()
    for r in rows:
        if exclude_library and r["library"] == exclude_library:
            continue
        v = vectors.get(r["heading"])
        if v is None:
            continue
        if r["canonical"] and r["canonical"] not in NO_CENTROID:
            if seen_name[(r["canonical"], r["heading"])] < 3:
                name_vecs[r["canonical"]].append(v)
                seen_name[(r["canonical"], r["heading"])] += 1
        elif not r["canonical"] and r["lane"] and r["lane"] not in ("other", "abstract") and r["depth"] == 1 and seen_rest[(r["lane"], r["heading"])] < 3:
            rest_vecs[r["lane"]].append(v)
            seen_rest[(r["lane"], r["heading"])] += 1
    names = {name: c for name, vs in name_vecs.items() if (c := _mean_unit(vs)) is not None and len(vs) >= 3}
    lanes: dict[str, list[float]] = {}
    for name, c in names.items():
        lane = CANON[name][0]
        if lane not in ("other", "abstract"):
            lanes[f"{lane}/{name}"] = c
    for lane, vs in rest_vecs.items():
        c = _mean_unit(vs)
        if c is not None and len(vs) >= 5:
            lanes[f"{lane}/rest"] = c
    return lanes, names, {k: len(v) for k, v in rest_vecs.items()}, {k: len(v) for k, v in name_vecs.items()}


def make_centroids(rows: list[dict[str, Any]], out: Path, url: str | None = None, model: str | None = None, thresholds: dict[str, list[float]] | None = None) -> dict[str, Any]:
    """`data/headings.json`: the lane centroids and the canonical-name centroids from the whole
    harvest, the modal spelling per canonical name, and the thresholds `--measure` chose."""
    from . import __version__
    from .meaning import QUERY

    vectors, used = _embed_unique([r["heading"] for r in rows], url, model)
    lanes, names, lane_counts, name_counts = centroids_from(rows, vectors)
    modal: dict[str, str] = {}
    spellings: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r["canonical"]:
            spellings[r["canonical"]][r["heading"]] += 1
    for name, c in spellings.items():
        modal[name] = c.most_common(1)[0][0]
    data = {
        "model": used,
        "prefix": QUERY,
        "made": time.strftime("%Y-%m-%d"),
        "parser": __version__,
        "libraries": sorted({r["library"] for r in rows}),
        "from": f"{len(rows)} titled sections of the libraries' JATS files: one centroid per canonical name from the headings the catalogue matched, the lane prototypes those centroids grouped by lane plus one 'rest' centroid per lane from the headings a sec-type laned and the catalogue did not name; no text but the modal spellings",
        "thresholds": thresholds or {"heading": [0.75, 0.05], "canonical": [0.75, 0.05]},
        "lanes": {"centroids": lanes, "counts": lane_counts},
        "canonical": {"centroids": names, "counts": name_counts, "lane": {n: CANON[n][0] for n in names}, "modal": modal},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")), "utf-8")
    return data


def measure(rows: list[dict[str, Any]], url: str | None = None, model: str | None = None, grid: tuple[tuple[float, float], ...] = ((0.6, 0.03), (0.65, 0.03), (0.7, 0.03), (0.7, 0.05), (0.75, 0.03), (0.75, 0.05), (0.8, 0.05))) -> dict[str, Any]:
    """Library-out: centroids from every library but one, then that library's top-level headings
    the vocabulary names (truth: the vocabulary's lane) and every heading the catalogue names
    (truth: the catalogue's name) are asked of the centroids at each threshold and margin —
    precision and recall per lane and per name, and what the centroids would call the headings
    that have no truth (the vocabulary's `other`, the catalogue's silence), for the eye."""
    from .meaning import _decide, _rank

    vectors, used = _embed_unique([r["heading"] for r in rows], url, model)
    libs = sorted({r["library"] for r in rows})
    out: dict[str, Any] = {"model": used, "rows": len(rows), "unique": len(vectors), "libraries": libs, "grid": {}}
    unlabelled_lane: Counter = Counter()
    unlabelled_name: Counter = Counter()
    samples: dict[str, Counter] = defaultdict(Counter)
    sample_at = grid[min(2, len(grid) - 1)]
    out["sample_at"] = f"{sample_at[0]}/{sample_at[1]}"
    for t, m in grid:
        lane_c = Counter()
        name_c = Counter()
        for lib in libs:
            lanes, names, _, _ = centroids_from(rows, vectors, exclude_library=lib)
            lane_protos = list(lanes.items())
            name_protos = list(names.items())
            if not lane_protos or not name_protos:
                continue  # a fold with nothing to compare against: too small a harvest
            for r in rows:
                if r["library"] != lib:
                    continue
                v = vectors[r["heading"]]
                if r["depth"] == 1:
                    truth = role_of(r["heading"], meaning=False)
                    verdict = _decide(_rank(v, lane_protos), t, m, {})
                    if truth != "other":
                        lane_c[("answered", truth)] += int(verdict.sure)
                        lane_c[("named", verdict.name)] += int(verdict.sure)
                        lane_c[("correct", truth)] += int(verdict.sure and verdict.name == truth)
                        lane_c[("truth", truth)] += 1
                    elif (t, m) == sample_at:
                        unlabelled_lane[verdict.name] += 1
                        if verdict.sure:
                            samples[f"lane:{verdict.name}"][r["heading"]] += 1
                cname, how = canonical_of(r["heading"])
                verdict = _decide(_rank(v, name_protos), t, m, {})
                if cname is not None:
                    name_c[("answered", cname)] += int(verdict.sure)
                    name_c[("named", verdict.name)] += int(verdict.sure)
                    name_c[("correct", cname)] += int(verdict.sure and verdict.name == cname)
                    name_c[("truth", cname)] += 1
                elif (t, m) == sample_at:
                    unlabelled_name[verdict.name] += 1
                    if verdict.sure:
                        samples[f"name:{verdict.name}"][r["heading"]] += 1

        def table(c: Counter, keys: list[str]) -> dict[str, Any]:
            per = {}
            for k in keys:
                named, correct, truth = c[("named", k)], c[("correct", k)], c[("truth", k)]
                if named or truth:
                    per[k] = {"truth": truth, "named": named, "correct": correct, "precision": round(correct / named, 3) if named else None, "recall": round(correct / truth, 3) if truth else None}
            named_all = sum(v for (kind, k), v in c.items() if kind == "named")
            correct_all = sum(v for (kind, k), v in c.items() if kind == "correct")
            truth_all = sum(v for (kind, k), v in c.items() if kind == "truth")
            return {"named": named_all, "correct": correct_all, "truth": truth_all, "precision": round(correct_all / named_all, 3) if named_all else None, "recall": round(correct_all / truth_all, 3) if truth_all else None, "per": per}

        out["grid"][f"{t}/{m}"] = {"lanes": table(lane_c, list(LANES)), "canonical": table(name_c, list(CANON))}
    out["unlabelled"] = {"lanes": dict(unlabelled_lane.most_common()), "canonical": dict(unlabelled_name.most_common())}
    out["samples"] = {k: c.most_common(8) for k, c in samples.items()}
    return out


def main(argv: list[str] | None = None) -> int:
    from .meaning import DATA

    ap = argparse.ArgumentParser(prog="litrag_parser.headings", description=__doc__.split("\n\n")[0])
    ap.add_argument("--lib", action="append", default=[], help="a library directory (repeatable)")
    ap.add_argument("--harvest", action="store_true", help="count (heading, lane, canonical) from the libraries' JATS files into <root>/headings.json")
    ap.add_argument("--make-centroids", action="store_true", help="write data/headings.json from the harvest")
    ap.add_argument("--measure", action="store_true", help="library-out precision of the centroids against the vocabulary and the catalogue")
    ap.add_argument("--threshold", type=float, nargs=2, metavar=("HEADING", "CANONICAL"), help="thresholds to write with --make-centroids")
    ap.add_argument("--margin", type=float, nargs=2, metavar=("HEADING", "CANONICAL"), help="margins to write with --make-centroids")
    ap.add_argument("--out", default=str(DATA / "headings.json"))
    ap.add_argument("--json", help="save the measurement here")
    args = ap.parse_args(argv)
    libs = [Path(l).expanduser() for l in args.lib]
    if not libs or not (args.harvest or args.make_centroids or args.measure):
        ap.print_help()
        return 2
    root = libs[0].resolve().parent
    store = root / "headings.json"
    if args.harvest or not store.exists():
        rows = harvest(libs)
        store.write_text(json.dumps(rows, ensure_ascii=False), "utf-8")
        by_how = Counter(r["how"] or "none" for r in rows)
        by_lane = Counter(r["lane"] or "none" for r in rows if r["depth"] == 1)
        print(f"harvested {len(rows)} titled sections from {len(libs)} libraries into {store}: canonical by {dict(by_how)}; top-level lanes {dict(by_lane.most_common())}")
    rows = json.loads(store.read_text("utf-8"))
    if args.measure:
        r = measure(rows)
        print(f"{r['rows']} sections, {r['unique']} unique headings, {r['model']}, library-out over {r['libraries']}")
        for key, g in r["grid"].items():
            print(f"  threshold/margin {key}: lanes precision {g['lanes']['precision']} recall {g['lanes']['recall']} (named {g['lanes']['named']} of {g['lanes']['truth']}) · canonical precision {g['canonical']['precision']} recall {g['canonical']['recall']} (named {g['canonical']['named']} of {g['canonical']['truth']})")
            for which in ("lanes", "canonical"):
                worst = sorted(((v["precision"], k, v) for k, v in g[which]["per"].items() if v["precision"] is not None), key=lambda x: x[0])[:4]
                print(f"      {which} weakest: " + "; ".join(f"{k} {v['precision']} ({v['correct']}/{v['named']}, recall {v['recall']})" for _, k, v in worst))
        print(f"  the unlabelled, named by the centroids (at {r.get('sample_at')}):", r["unlabelled"])
        for k, s in r["samples"].items():
            print(f"      {k}: {s}")
        if args.json:
            Path(args.json).write_text(json.dumps(r, indent=1), "utf-8")
    if args.make_centroids:
        thresholds = None
        if args.threshold and args.margin:
            thresholds = {"heading": [args.threshold[0], args.margin[0]], "canonical": [args.threshold[1], args.margin[1]]}
        data = make_centroids(rows, Path(args.out), thresholds=thresholds)
        print(f"wrote {args.out}: lanes {data['lanes']['counts']} · canonical names {len(data['canonical']['centroids'])} · {data['model']} · thresholds {data['thresholds']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
