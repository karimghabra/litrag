"""The parsing worker: JSON lines in, JSON lines out.

The desktop app spawns one of these and talks to it over stdio. Every request
is one line — `{"id": ..., "op": ..., ...}` — and every answer is one or more
lines carrying the same id: `stage` and `working` events while a paper is
being read, `paper`/`tree` events as it lands, and a final `done` (or
`error`). Reads (`papers`, `tree`, `node`, `sql`) and small writes (`describe`,
`label`) are answered at once from the main thread; `ingest`, `reparse` and `rebuild` run one at a time on the
ingest thread, so the app can browse trees while Docling is busy.

Docling is imported lazily on the ingest thread — it takes seconds and pulls
torch — so a `hello` or `papers` comes back instantly.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from . import __version__, acquire, boundary, lanes
from . import projects as project_rows
from .library import Library, create_library, library_root, list_libraries, now_iso, open_library, parsed_papers, safe_key
from .citations import link_citations
from .edges import link_edges, summarize as summarize_edges
from .confidence import assess as assess_confidence
from .outline import enabled as outline_enabled, judge as judge_outline
from .figures import enabled as figures_enabled
from .paper_type import decide as decide_type
from .record import jats_authors, jats_journal, lookup_record
from .harness import pdf_title
from .judge import Judge, available as judge_available, default_model as judge_model
from .recover import recover_from_pdf
from .citations import summarize as summarize_citations
from .store import (cited_by, cites_of, edges_of, file_paper, list_papers, log_event, node, node_count, open_store, paper_tree, refs_of, run_select, save_edges, save_refs, save_tree, section, set_confidence, set_record, set_status, set_type, sha256_of, siblings)
from .tree import build_tree

_out_lock = threading.Lock()


def _has_a_tree(conn: Any, lib: Any, key: str) -> bool:
    """Whether a paper really is read: it has nodes, and the raw document a rebuild would
    derive them from again. Either missing and the row is a claim the store cannot honour."""
    return node_count(conn, key) > 0 and (lib.parsed_dir / f"{safe_key(key)}.docling.json").exists()


def emit(msg: dict[str, Any]) -> None:
    line = json.dumps(msg, ensure_ascii=False, default=str)
    with _out_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


class _LogForwarder(logging.Handler):
    """Docling's own log lines, forwarded as `log` events so the app can show them."""

    def __init__(self, worker: "Worker"):
        super().__init__(level=logging.INFO)
        self.worker = worker

    @property
    def current(self) -> dict[str, Any]:
        return self.worker.current

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        if record.name.startswith(("httpx", "urllib3", "filelock")):
            return
        emit({"event": "log", "id": self.current.get("id"), "paper": self.current.get("paper"), "logger": record.name, "level": record.levelname.lower(), "message": record.getMessage()})


_DOI = re.compile(r"\b10\.\d{4,9}/[^\s\"<>)\]]+")
_PMCID = re.compile(r"\bPMC\d{6,8}\b")


_XML_DOI = re.compile(r'<article-id[^>]*pub-id-type="doi"[^>]*>\s*([^<\s]+)\s*</article-id>')
_XML_PMCID = re.compile(r'<article-id[^>]*pub-id-type="pmcid"[^>]*>\s*(PMC\d+)\s*</article-id>')
_XML_PMID = re.compile(r'<article-id[^>]*pub-id-type="pmid"[^>]*>\s*(\d+)\s*</article-id>')
_JATS_DOCTYPE = b'<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Archiving and Interchange DTD v1.2 20190208//EN" "JATS-archivearticle1.dtd">'


def jats_ids(path: Path) -> tuple[str | None, str | None, str | None]:
    """DOI, PMCID and PMID from a JATS file's article-meta."""
    head = path.read_text("utf-8", errors="replace")[:20000]
    doi, pmc, pmid = _XML_DOI.search(head), _XML_PMCID.search(head), _XML_PMID.search(head)
    return (doi.group(1) if doi else None), (pmc.group(1) if pmc else None), (pmid.group(1) if pmid else None)


def jats_stream(path: Path) -> Any:
    """Europe PMC's JATS has no DOCTYPE, and Docling tells XML flavours apart by it: add one."""
    from docling.datamodel.base_models import DocumentStream
    import io

    from .jats_prep import prepare_jats

    raw = prepare_jats(path.read_bytes())  # formulas as text, numeric citations bracketed — see jats_prep.py
    if b"<!DOCTYPE" not in raw[:2000]:
        head, sep, rest = raw.partition(b"?>")
        raw = (head + sep + b"\n" + _JATS_DOCTYPE + rest) if sep else _JATS_DOCTYPE + b"\n" + raw
    return DocumentStream(name=path.name, stream=io.BytesIO(raw))


#: Repositories mint DOIs too, and a paper prints the one for its data or its code on its first
#: page, often above its own: Zenodo, figshare, OSF, Dryad, Mendeley Data, Harvard Dataverse.
_REPOSITORY_DOI = re.compile(r"^10\.(?:5281|6084|17605|5061|17632|7910)/", re.I)


def pick_doi(text: str) -> str | None:
    """The paper's own DOI among those printed on its first pages: the first whose suffix carries
    a digit ("10.1073/pnas" at a line's end is the prefix of one, not a DOI) and that is not a
    repository's — unless a repository's is all there is, which is a report filed there. Found
    when a PDF was filed under its dataset's Zenodo DOI and so never met its own XML."""
    found = [m.group(0).rstrip(".,;:") for m in _DOI.finditer(text) if any(ch.isdigit() for ch in m.group(0).split("/", 1)[1])]
    own = [d for d in found if not _REPOSITORY_DOI.match(d)]
    return (own or found or [None])[0]


def sniff_ids(path: Path) -> tuple[str | None, str | None]:
    """The DOI and PMCID on a PDF's first pages, if printed there — else in its own file name.

    A paper that prints neither is filed under its content hash, and a hash meets nothing: it
    never finds the same paper's JATS, so the pair that would have been its witness is lost with
    nothing said. Measured on the campaign's corpus, 15 of 334 PDFs printed no identifier at all
    on their first two pages — and every one of them was *named* `PMC…​.pdf`.

    The file's name is a property of the file, not of a publisher, so reading it transfers. It is
    tried last, because what a paper prints about itself beats what someone called the file."""
    if path.suffix.lower() != ".pdf":
        return None, None
    from .recover import open_pdf

    text = ""
    try:
        with open_pdf(path) as pdf:
            for i in range(min(2, len(pdf))):
                text += pdf[i].get_textpage().get_text_range() + "\n"
    except Exception:  # a PDF pdfium cannot open still gets its name read below
        pass
    doi, pmc = pick_doi(text), _PMCID.search(text)
    if doi or pmc:
        return doi, (pmc.group(0) if pmc else None)
    named = path.stem.replace("_", "/")  # a DOI saved as a file name has its slash swapped
    pmc = _PMCID.search(path.stem)
    return pick_doi(named), (pmc.group(0) if pmc else None)


def guess_title(path: Path) -> str | None:
    """A PDF's title: its Title metadata when that is a title, else the largest line of
    text on the first page, when that is a title — four words or more, not furniture."""
    from .harness import pdf_title

    title = pdf_title(path)
    if title:
        return title
    from .recover import open_pdf

    try:
        rows: list[tuple[str, list[float]]] = []
        with open_pdf(path) as pdf:
            if not len(pdf):
                return None
            tp = pdf[0].get_textpage()
            n = tp.count_chars()
            text = tp.get_text_range(0, n)
            start = 0
            for raw in text.split("\r\n"):
                end = start + len(raw)
                heights = []
                for j in range(start, min(end, n)):
                    if not text[j].isspace():
                        l, b, r, t = tp.get_charbox(j)
                        if t > b:
                            heights.append(t - b)
                start = end + 2
                rows.append((raw, heights))
    except Exception:
        return None
    best: tuple[float, str] | None = None
    for raw, heights in rows:
        line = " ".join(raw.split())
        words = re.findall(r"[A-Za-z]{2,}", line)
        if len(heights) < 8 or len(words) < 4 or len(words) > 40 or re.search(r"\bvol\b|\bpp\.|©|journal|received|accepted|doi|www\.|http", line, re.I):
            continue
        size = sorted(heights)[len(heights) // 2]
        if best is None or size > best[0] + 0.5:
            best = (size, line)
    return best[1] if best else None


def lookup_by_title(title: str | None, timeout: float = 6.0) -> tuple[str | None, str | None]:
    """Europe PMC's answer to a title: the DOI and PMID of the one record whose title is the
    same words, else nothing. Offline, or in doubt, nothing — the paper keeps a hash key."""
    if not title or len(title.split()) < 4:
        return None, None
    import json as _json
    import urllib.parse
    import urllib.request

    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()  # noqa: E731
    query = urllib.parse.quote(f'TITLE:"{title}"')
    base = (os.environ.get("LITRAG_EPMC_URL") or "https://www.ebi.ac.uk/europepmc/webservices/rest").rstrip("/")
    url = f"{base}/search?query={query}&format=json&resultType=lite&pageSize=5"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None, None
    hits = [h for h in (data.get("resultList") or {}).get("result") or [] if norm(h.get("title") or "") == norm(title)]
    if len(hits) != 1:
        return None, None
    hit = hits[0]
    doi = (hit.get("doi") or "").strip() or None
    pmid = (hit.get("pmid") or "").strip() or None
    return doi, pmid


def page_count(path: Path) -> int | None:
    if path.suffix.lower() != ".pdf":
        return None
    from .recover import open_pdf

    try:
        with open_pdf(path) as pdf:
            return len(pdf)
    except Exception:
        return None


class Worker:
    def __init__(self, root: Path):
        self.root = root
        self.ingest_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self.current: dict[str, Any] = {}
        self._converter: Any = None
        self._device: str | None = None
        self.docling_version: str | None = None
        self._scorer: Any = None
        self._scorer_tried = False
        self._reported_down = False
        self._recovered: set[Path] = set()  # libraries whose interrupted papers have been closed
        self._recover_lock = threading.Lock()
        self._layout: Any = None  # the Docling child, spawned on the first paper and respawned when it dies
        self._embedder: Any = None  # retrieve.OllamaEmbedder, built on the first paper read

    @staticmethod
    def _meaning() -> dict[str, Any] | None:
        """The oracle's summary for an event: what it named, whether it was reachable."""
        o = lanes.active()
        if o is None:
            return None
        s = o.summary()
        return {"kinds": s["kinds"], "down": s["down"], "error": s["error"]}

    @staticmethod
    def _note_events(conn: Any, key: str, tree: Any) -> None:
        """What the reader noticed and did not act on, as rows in `events` beside the paper's —
        this reading's notes replacing the last reading's, so a rebuild twice is one set."""
        with conn:
            conn.execute("DELETE FROM events WHERE paper = ? AND (stage LIKE 'lane-%' OR stage LIKE 'type-%' OR stage LIKE 'outline-%')", (key,))
        for note in tree.notes:
            log_event(conn, key, now_iso(), str(note.get("kind", "note")), f"{note.get('node_id')}: {note.get('message')}")

    def scorer(self) -> Any:
        """The boundary scorer (boundary.py), built once and only when it is enabled and its
        model can be had; None otherwise. Never consulted by a plain `rebuild`."""
        if not self._scorer_tried:
            self._scorer_tried = True
            if boundary.enabled():
                s = boundary.BoundaryScorer()
                self._scorer = s if s.available() else None
        return self._scorer

    def embed_paper(self, conn: Any, key: str) -> dict[str, Any] | None:
        """A paper's passages embedded as soon as its rows are saved (retrieve.py), so the Query
        tab never waits on a library-wide pass: a rebuild replaces the nodes and their vectors go
        with them. Off with the oracle (`LITRAG_LANES=off`, which the tests set) or with
        `LITRAG_EMBED=off`; the embedder being down costs nothing but a note, and the Query tab's
        Embed button catches up later."""
        if os.environ.get("LITRAG_EMBED", "on") == "off" or os.environ.get("LITRAG_LANES", "on") == "off":
            return None
        from . import retrieve

        if self._embedder is None:
            self._embedder = retrieve.OllamaEmbedder()
        try:
            return retrieve.embed_library(conn, self._embedder, paper=key)
        except Exception as e:  # noqa: BLE001 — a passage not embedded is a search that misses it, never a paper lost
            return {"error": f"{type(e).__name__}: {e}"}

    # ---- the converter, built once on the ingest thread -------------------------------

    def converter(self, req_id: str) -> Any:
        if self._converter is not None:
            return self._converter
        emit({"event": "stage", "id": req_id, "stage": "models", "message": "Loading Docling and its models (first run downloads them)"})
        t = time.time()
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        import docling  # noqa: F401
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        try:
            from importlib.metadata import version

            self.docling_version = version("docling")
        except Exception:
            self.docling_version = "?"
        opts = PdfPipelineOptions(do_ocr=False, do_table_structure=True, generate_page_images=False)
        opts.table_structure_options.do_cell_matching = True
        artifacts = self.root / "models" / "docling"
        if artifacts.exists() and any(artifacts.iterdir()):
            opts.artifacts_path = artifacts
        self._converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.XML_JATS],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)},
        )
        try:
            from docling.utils.accelerator_utils import decide_device

            self._device = decide_device(opts.accelerator_options.device)
        except Exception:
            self._device = None
        emit({"event": "stage", "id": req_id, "stage": "models", "message": f"Docling {self.docling_version} ready on {self._device or 'cpu'}", "seconds": round(time.time() - t, 1), "device": self._device})
        return self._converter

    # ---- ingest ----------------------------------------------------------------------

    def ingest_loop(self) -> None:
        while True:
            req = self.ingest_queue.get()
            try:
                op = req["op"]
                if op == "ingest":
                    self.do_ingest(req)
                elif op == "reparse":
                    self.do_reparse(req)
                elif op == "rebuild":
                    self.do_rebuild(req)
                elif op == "judge":
                    self.do_rebuild({**req, "judge": True})  # the rows again, with the local model reading the pairs the rules leave
                elif op == "fetch":
                    self.do_fetch(req)
                elif op == "merge":
                    self.do_merge(req)
                elif op == "embed":
                    self.do_embed(req)
                elif op == "model_label":
                    self.do_model_label(req)
                elif op == "figures":
                    self.do_figures(req)
            except Exception as e:  # never let one paper kill the worker
                emit({"event": "error", "id": req.get("id"), "op": req.get("op"), "lib": req.get("lib"), "message": str(e), "trace": traceback.format_exc()})
            finally:
                self.current = {}

    def _lib(self, req: dict[str, Any]) -> Library:
        lib = open_library(self.root, str(req.get("lib", "")))
        if lib is None:
            raise ValueError(f"No library {req.get('lib')!r} under {self.root}")
        lib.ensure_dirs()
        self._close_interrupted(lib)
        return lib

    def _close_interrupted(self, lib: Library) -> None:
        """A paper left `parsing` by a worker that died is given its terminal state back.

        `parsing` is set before the layout stage and cleared only by `save_tree`, so a native
        crash or a `quit` mid-paper leaves it set for good: the card's progress bar never stops,
        and nothing ever reports the paper as anything. Every file handed to `ingest` has to end
        in a state with a reason, so an interrupted paper is `failed` — which the window already
        shows with its error, and which `ingest` and `reparse` will both pick up again."""
        # `_lib` is called from the stdin thread as well as the ingest thread, so the check and
        # the add have to be one step: otherwise a read op could run this while a paper is being
        # parsed and mark that very paper `failed` underneath it.
        with self._recover_lock:
            if lib.dir in self._recovered:
                return
            self._recovered.add(lib.dir)
        try:
            conn = open_store(lib.store_path)
        except Exception:  # a library with no store yet has nothing to recover
            return
        try:
            # never the paper this worker is reading right now: it is legitimately `parsing`
            in_flight = {self.current.get("paper")}
            stuck = [r["key"] for r in conn.execute("SELECT key FROM papers WHERE status = 'parsing'")
                     if r["key"] not in in_flight]
            for key in stuck:
                set_status(conn, key, "failed", error="the worker stopped while reading this paper")
                log_event(conn, key, now_iso(), "interrupted", "left parsing by a worker that did not finish")
            if stuck:
                emit({"event": "recovered", "lib": lib.id, "papers": stuck,
                      "reason": "left parsing by a worker that did not finish"})
        finally:
            conn.close()

    def do_ingest(self, req: dict[str, Any]) -> dict[str, str]:
        """File and read the given files; returns `{path: key}` for every file filed. `known`
        (path → {doi, pmid, pmcid}) is what the caller already knows of a file — a fetch knows the
        candidate it came for — and fills whatever the file does not say of itself."""
        lib = self._lib(req)
        req_id = req.get("id")
        conn = open_store(lib.store_path)
        filed: list[tuple[str, Path]] = []
        figure_sources: list[str] = []  # XML papers given a PDF for their figures
        keys: dict[str, str] = {}
        known_all: dict[str, dict[str, Any]] = req.get("known") or {}
        for raw in req.get("paths", []):
            src = Path(raw)
            if not src.exists() or src.suffix.lower() not in (".pdf", ".xml"):
                emit({"event": "skipped", "id": req_id, "path": str(src), "reason": "not a PDF or JATS XML file"})
                continue
            sha = sha256_of(src)
            fmt = "pdf" if src.suffix.lower() == ".pdf" else "jats"
            pmid = None
            if fmt == "jats":
                doi, pmcid, pmid = jats_ids(src)
            else:
                doi, pmcid = sniff_ids(src)
                known = known_all.get(str(raw)) or {}
                if not doi and not pmcid and (known.get("doi") or known.get("pmcid")):
                    doi, pmcid, pmid = known.get("doi"), known.get("pmcid"), known.get("pmid")  # the candidate this file was fetched for
                if not doi and not pmcid and not req.get("offline"):
                    doi, pmid = lookup_by_title(guess_title(src))  # Europe PMC, by the paper's own title; nothing when offline or unsure
                    if doi or pmid:
                        emit({"event": "identified", "id": req_id, "path": str(src), "doi": doi, "pmid": pmid})
            known = known_all.get(str(raw)) or {}
            result = file_paper(conn, title=src.stem, file="", sha256=sha, fmt=fmt, doi=doi, pmid=pmid or known.get("pmid"), pmcid=pmcid or known.get("pmcid"), now=now_iso())
            keys[str(raw)] = result.key
            row = conn.execute("SELECT status, file FROM papers WHERE key = ?", (result.key,)).fetchone()
            if result.existed and fmt == "pdf" and row and (row["file"] or "").lower().endswith(".xml") and not req.get("reread"):
                # The paper is its XML: the text, its structure. A PDF of it is kept beside it for
                # what the XML only names — its figures, drawn — and read for their numbers.
                dest = lib.papers_dir / f"{_safe(result.key)}.figures.pdf"
                if src.resolve() != dest.resolve():
                    shutil.copy2(src, dest)
                    if src.parent.resolve() == lib.inbox_dir.resolve():
                        src.unlink()
                conn.execute("UPDATE papers SET figures_file = ? WHERE key = ?", (dest.name, result.key))
                conn.commit()
                log_event(conn, result.key, now_iso(), "filed", f"a PDF of the XML, kept for its figures: {src.name}")
                emit({"event": "paper", "id": req_id, "lib": lib.id, "paper": result.key, "existed": True, "kept": True, "figures_file": dest.name, "doi": doi, "pmcid": pmcid,
                      "file": row["file"], "status": row["status"]})
                figure_sources.append(result.key)
                continue
            # A paper already read stays as it was read — the same DOI arriving as a second file
            # (a PDF after its JATS, say) is noted, not swapped in. `reread` is the way to replace it.
            #
            # "Already read" is a fact about the tree, not about the row that says one was meant.
            # Keying it on `status` alone is what made the worker's native crash silent and
            # permanent: seventeen PDFs kept a `parsed` row with no nodes under it, every later
            # ingest read the row and skipped the file, and `rebuild` never visits a paper whose
            # raw document is gone. A paper with no nodes, or whose raw Docling document has been
            # lost, is not read, whatever the row says, and is parsed again.
            already = bool(result.existed and row and row["status"] == "parsed" and row["file"] and not req.get("reread"))
            if already and not _has_a_tree(conn, lib, result.key):
                already = False
                log_event(conn, result.key, now_iso(), "unfiled", "filed as parsed with no tree: reading it again")
            if already:
                dest = lib.papers_dir / row["file"]
            else:
                dest = lib.papers_dir / f"{_safe(result.key)}{src.suffix.lower()}"
                if src.resolve() != dest.resolve():
                    shutil.copy2(src, dest)
                    if src.parent.resolve() == lib.inbox_dir.resolve():
                        src.unlink()  # the inbox is a doorway, not a shelf
                conn.execute("UPDATE papers SET file = ?, format = ?, sha256 = ? WHERE key = ?", (dest.name, fmt, sha, result.key))
            conn.commit()
            if not already and not req.get("offline") and (doi or pmid or pmcid):
                record = lookup_record(doi, pmid, pmcid=pmcid)  # Europe PMC's word: who wrote it, where, when, and what kind of paper it is — kept with the paper
                if record:
                    set_record(conn, result.key, **record)
            log_event(conn, result.key, now_iso(), "filed", f"{'seen before, kept' if already else 'seen before' if result.existed else 'new'}: {src.name}")
            emit({"event": "paper", "id": req_id, "lib": lib.id, "paper": result.key, "existed": result.existed, "kept": already, "doi": doi, "pmcid": pmcid, "file": dest.name, "status": "parsed" if already else "queued", "pages": page_count(dest)})
            if not already:
                filed.append((result.key, dest))
        conn.close()
        for i, (key, path) in enumerate(filed):
            emit({"event": "progress", "id": req_id, "op": "ingest", "lib": lib.id, "done": i, "total": len(filed), "label": f"Reading paper {i + 1} of {len(filed)}"})
            self.parse_one(lib, key, path, req_id, ask_judge=bool(req.get("judge")), ask_outline=bool(req.get("outline")) or outline_enabled())
        parsed_now = {k for k, _ in filed}
        for key in figure_sources:
            if key not in parsed_now:  # read already: its figures now; one read in this batch read them as it was saved
                self.figures_of(lib, key, req_id)
        conn = open_store(lib.store_path)
        try:
            acquire.reconcile(conn)  # a candidate whose paper is now filed is `ingested`, whichever way the file came
        finally:
            conn.close()
        emit({"event": "done", "id": req_id, "op": "ingest", "lib": lib.id, "parsed": [k for k, _ in filed]})
        return keys

    def do_reparse(self, req: dict[str, Any]) -> None:
        lib = self._lib(req)
        conn = open_store(lib.store_path)
        keys = req.get("keys") or [r["key"] for r in conn.execute("SELECT key FROM papers WHERE file IS NOT NULL")]
        files = {r["key"]: r["file"] for r in conn.execute("SELECT key, file FROM papers")}
        conn.close()
        for key in keys:
            if key in files and files[key]:
                self.parse_one(lib, key, lib.papers_dir / files[key], req.get("id"), ask_judge=bool(req.get("judge")), ask_outline=bool(req.get("outline")) or outline_enabled())
        emit({"event": "done", "id": req.get("id"), "op": "reparse", "parsed": keys})

    def do_rebuild(self, req: dict[str, Any]) -> None:
        """Rows again from the raw parses, without Docling — the tree builder changed."""
        lib = self._lib(req)
        conn = open_store(lib.store_path)
        rebuilt: list[str] = []
        refused: list[dict[str, str]] = []
        keys = set(req.get("keys") or [])
        rows = parsed_papers(lib.dir, sorted(keys) or None)
        for i, row in enumerate(rows):
            key = row["key"]
            source = row["source"]
            if i % 5 == 0 or i == len(rows) - 1:
                emit({"event": "progress", "id": req.get("id"), "op": req.get("op", "rebuild"), "lib": lib.id, "done": i, "total": len(rows), "label": f"Deriving the rows again: {lib.id}"})
            try:
                self._rebuild_one(conn, lib, req, row, key, source)
            except Exception as e:  # noqa: BLE001
                # One paper must not cost the rest: a raw document that is truncated — a child
                # killed mid-write before the write became atomic, a disk that filled — would
                # otherwise abort the whole rebuild on the first bad file, which is the same
                # "one paper costs the run" shape the layout child was built to remove.
                refused.append({"paper": key, "reason": f"{type(e).__name__}: {e}"})
                log_event(conn, key, now_iso(), "rebuild-refused", str(e)[:400])
                emit({"event": "stage", "id": req.get("id"), "lib": lib.id, "paper": key, "stage": "failed",
                      "message": f"rebuild: {e}", "trace": traceback.format_exc()})
                continue
            rebuilt.append(key)
        conn.close()
        emit({"event": "done", "id": req.get("id"), "op": req.get("op", "rebuild"), "lib": lib.id,
              "rebuilt": rebuilt, "refused": refused})

    def _rebuild_one(self, conn: Any, lib: Library, req: dict[str, Any], row: dict[str, Any],
                     key: str, source: Any) -> None:
        hint = pdf_title(source) if row["format"] == "pdf" and source and source.exists() else None
        ask = bool(req.get("judge"))
        judge = Judge(conn, key, ask_model=ask and judge_available(), model=judge_model(lib.dir), scorer=self.scorer() if ask else None)
        doc = json.loads(row["raw"].read_text("utf-8"))
        recover_from_pdf(doc, source if row["format"] == "pdf" else None)
        tree = build_tree(doc, key, title_hint=hint, judge=judge, record={"journal": row.get("journal")})
        if outline_enabled() or req.get("outline"):
            judge_outline(tree, conn, key, ask_model=ask, pub_types=row["pub_types"])  # a rebuild replays the outline's row; only the judge op asks the model
        n = save_tree(conn, key, tree, parser=f"{'judge ' + judge.model if ask else 'rebuild'} {__version__}", parsed_at=now_iso(), seconds=0.0)
        from .figures import remap as remap_figures

        remap_figures(conn, key)  # the charts stay; their figures are found again by their place on the page
        xml = source.read_bytes() if row["format"] == "jats" and source and source.exists() else None
        if xml:
            journal, year = jats_journal(xml)
            set_record(conn, key, authors=jats_authors(xml), journal=journal, year=year, overwrite=True)  # the file's own word on who wrote it, where and when
        refs, cites = link_citations(tree, xml)
        save_refs(conn, key, refs, cites)
        self.embed_paper(conn, key)  # after the reference list is linked: its entries are no passages
        edges = link_edges(tree, key, lanes.active())
        save_edges(conn, key, edges)
        kind = decide_type(tree, jats_xml=xml, pub_types=row["pub_types"], oracle=lanes.active())
        set_type(conn, key, kind["type"], kind["source"], kind["detail"], kind.get("subtype"))
        tree.notes.extend(kind.get("notes", []))
        self._note_events(conn, key, tree)
        sure = assess_confidence(tree, kind)
        set_confidence(conn, key, sure["confidence"], sure["reasons"], sure["penalties"])
        emit({"event": "tree", "id": req.get("id"), "lib": lib.id, "paper": key, "title": tree.title, "type": kind, "confidence": {"confidence": sure["confidence"], "reasons": sure["reasons"]}, "roles": tree.roles, "has_methods": tree.has_methods, "nodes": n, "pages": len(tree.pages), "dropped": tree.dropped, "repairs": tree.repairs, "notes": len(tree.notes), "judged": judge.summary(), "meaning": self._meaning(), "edges": summarize_edges(tree, edges), **summarize_citations(refs, cites)})

    def layout(self, lib: Library, key: str, path: Path, raw_path: Path, req_id: Any, started: float, stage: Any) -> dict[str, Any]:
        """The raw Docling document, from a child process the worker can lose.

        A native crash inside Docling kills whatever process it happens in, so running it here
        would cost the whole run — every paper still queued — rather than the one paper it lands
        on. `LITRAG_LAYOUT_CHILD=off` converts in this process instead, which is what the tests
        that have no Docling do, and is the way back if the child ever misbehaves."""
        from .layout import LayoutChild, enabled as child_enabled

        def beat() -> None:
            emit({"event": "working", "id": req_id, "paper": key, "stage": "layout", "elapsed": round(time.time() - started, 1)})

        if child_enabled():
            if self._layout is None:
                self._layout = LayoutChild(self.root)
            # repointed per paper: the child outlives the paper, so a callback captured when it
            # was spawned would file every later paper's Docling log under the first paper's id
            self._layout.on_log = lambda line: emit(
                {"event": "log", "id": req_id, "paper": key, "logger": "layout", "level": "info", "message": line})
            stage("layout", "Reading the layout: headings, paragraphs, tables, figures")
            self._layout.convert(
                path, raw_path, heartbeat=beat,
                on_retry=lambda why: stage("layout", f"{why}: reading it again in a fresh child"),
            )
            return json.loads(raw_path.read_text("utf-8"))

        from .layout import _convert

        converter = self.converter(req_id)
        self.current = {"id": req_id, "paper": key}
        stage("layout", "Reading the layout: headings, paragraphs, tables, figures")
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["doc"] = _convert(converter, path)
            except Exception as e:  # reported below on the main path
                box["error"] = e

        th = threading.Thread(target=run, daemon=True)
        th.start()
        while th.is_alive():
            th.join(1.5)
            if th.is_alive():
                beat()
        if "error" in box:
            raise box["error"]
        doc = box["doc"]
        raw_path.write_text(json.dumps(doc, ensure_ascii=False), "utf-8")
        return doc

    def parse_one(self, lib: Library, key: str, path: Path, req_id: Any, ask_judge: bool = False, ask_outline: bool = False) -> None:
        self.current = {"id": req_id, "paper": key}
        conn = open_store(lib.store_path)
        started = time.time()

        def stage(name: str, message: str, **extra: Any) -> None:
            log_event(conn, key, now_iso(), name, message)
            emit({"event": "stage", "id": req_id, "lib": lib.id, "paper": key, "stage": name, "message": message, "elapsed": round(time.time() - started, 1), **extra})

        try:
            set_status(conn, key, "parsing")
            stage("opening", f"Opening {path.name}", pages=page_count(path))
            raw_path = lib.parsed_dir / f"{_safe(key)}.docling.json"
            doc = self.layout(lib, key, path, raw_path, req_id, started, stage)
            stage("tree", "Building the tree and assigning facets", texts=len(doc.get("texts", [])), tables=len(doc.get("tables", [])), pictures=len(doc.get("pictures", [])))
            ask = ask_judge or os.environ.get("LITRAG_JUDGE") == "1"
            scorer = self.scorer()
            judge = Judge(conn, key, ask_model=ask, model=judge_model(lib.dir), scorer=scorer)
            if ask and not judge_available(judge.url):
                stage("judge", f"Ollama is not answering at {judge.url}: paragraphs split at page breaks stay split where the rules cannot join them")
                judge.ask_model = False
            elif ask:
                stage("judge", f"Reading adjacent blocks with {judge.model}" + (f", after the boundary scorer ({scorer.model_name})" if scorer else ""))
            elif scorer is not None:
                stage("judge", f"Scoring the page breaks the rules leave open with {scorer.model_name}")
            recovery = recover_from_pdf(doc, path)
            if recovery and any(recovery.values()):
                stage("recover", "The text layer's lines the layout model missed: " + ", ".join(f"{v} {k}" for k, v in recovery.items() if v), **recovery)
            journal = conn.execute("SELECT journal FROM papers WHERE key = ?", (key,)).fetchone()
            tree = build_tree(doc, key, title_hint=pdf_title(path) if path.suffix.lower() == ".pdf" else None, judge=judge, record={"journal": journal[0] if journal else None})
            outlined: dict[str, Any] = {}
            if ask_outline:
                from .outline import default_model as outline_model

                stage("outline", f"Reading the whole paper for its outline with {outline_model()}")
                first = conn.execute("SELECT pub_types FROM papers WHERE key = ?", (key,)).fetchone()
                outlined = judge_outline(tree, conn, key, ask_model=True, pub_types=first["pub_types"] if first else None)
                if outlined.get("error") or outlined.get("unreadable"):
                    stage("outline", f"The outline judge gave no usable answer ({outlined.get('error') or 'the answer was not an outline'}): the reader's own structure stands")
            seconds = round(time.time() - started, 1)
            n = save_tree(conn, key, tree, parser=f"docling {self.docling_version} · litrag-parser {__version__}", parsed_at=now_iso(), seconds=seconds)
            xml = path.read_bytes() if path.suffix.lower() == ".xml" else None
            if xml:
                journal, year = jats_journal(xml)
                set_record(conn, key, authors=jats_authors(xml), journal=journal, year=year, overwrite=True)  # the file's own word on who wrote it, where and when
            refs, cites = link_citations(tree, xml)
            save_refs(conn, key, refs, cites)
            embedded = self.embed_paper(conn, key)  # after the reference list is linked: its entries are no passages
            if embedded and embedded.get("embedded"):
                stage("embedded", f"{embedded['embedded']} passages embedded for search")
            edges = link_edges(tree, key, lanes.active())
            save_edges(conn, key, edges)
            linked = summarize_edges(tree, edges)
            stored = conn.execute("SELECT pub_types FROM papers WHERE key = ?", (key,)).fetchone()
            kind = decide_type(tree, jats_xml=xml, pub_types=stored["pub_types"] if stored else None, oracle=lanes.active())
            set_type(conn, key, kind["type"], kind["source"], kind["detail"], kind.get("subtype"))
            tree.notes.extend(kind.get("notes", []))
            self._note_events(conn, key, tree)
            sure = assess_confidence(tree, kind)
            set_confidence(conn, key, sure["confidence"], sure["reasons"], sure["penalties"])
            o = lanes.active()
            if o is not None and o.summary()["down"] and not self._reported_down:
                self._reported_down = True
                stage("meaning", f"The embedder is not answering ({o.summary()['error']}): texts the vocabulary does not know read as `other` and are not stored; a rebuild once it answers will read them")
            if figures_enabled():
                if path.suffix.lower() == ".pdf":
                    self.read_figures(conn, key, path, stage)
                else:
                    beside = conn.execute("SELECT figures_file FROM papers WHERE key = ?", (key,)).fetchone()
                    if beside and beside["figures_file"] and (path.parent / beside["figures_file"]).exists():
                        self.read_figures(conn, key, path.parent / beside["figures_file"], stage, pages=True)
            links = summarize_citations(refs, cites)
            judged = judge.summary()
            stage("saved", f"{n} nodes, {links['refs']} references, {links['citations']} citation links, {linked['linked']} of {linked['findings']} findings linked to a method, a {kind['type']} paper by its {kind['source']}, confidence {sure['confidence']}" + (f" ({sure['reasons'][0]})" if sure["reasons"] else "") + (f", outline by {outlined['model']}: {outlined.get('lanes', 0)} lanes, {outlined.get('built', 0)} headings built" if outlined.get("sections") is not None else "") + (f", {judged['joined']} of {judged['asked']} judged pairs joined" if judged["asked"] else "") + f" in {seconds}s", nodes=n, **links, judged=judged, edges=linked, type=kind)
            emit({"event": "tree", "id": req_id, "lib": lib.id, "paper": key, "title": tree.title, "type": kind, "confidence": {"confidence": sure["confidence"], "reasons": sure["reasons"]}, "roles": tree.roles, "has_methods": tree.has_methods, "nodes": n, "pages": len(tree.pages), "seconds": seconds, "raw": str(raw_path), "dropped": tree.dropped, "repairs": tree.repairs, "notes": len(tree.notes), "judged": judged, "meaning": self._meaning(), "edges": linked, **links})
        except Exception as e:
            set_status(conn, key, "failed", error=str(e))
            log_event(conn, key, now_iso(), "failed", str(e))
            emit({"event": "stage", "id": req_id, "lib": lib.id, "paper": key, "stage": "failed", "message": str(e), "trace": traceback.format_exc(), "elapsed": round(time.time() - started, 1)})
        finally:
            conn.close()

    def read_figures(self, conn: Any, key: str, path: Path, stage: Any, pages: bool = False) -> dict[str, Any] | None:
        """A PDF's figures read into numbers (figures.py) — the paper's own, or (`pages`) the PDF
        kept beside its XML; a failure is said and never fails the paper."""
        from . import figures

        try:
            n = len(figures.figure_numbers(conn, key) if pages else figures.pictures(conn, key))
            if not n:
                return None
            stage("figures", f"Reading the charts in {n} figure{'s' if n != 1 else ''}" + (f" from {path.name}" if pages else ""))
            out = figures.read_pages(conn, key, path) if pages else figures.read_paper(conn, key, path)
            stage("figures", f"{out['read']} of {out['plots']} charts read in {out['figures']} figures: {out['values']} values, in {out['seconds']}s"
                  + (f"; {out['unmatched']} plots under no caption of the paper's" if out.get("unmatched") else ""), **out)
            return out
        except Exception as e:  # noqa: BLE001 — the paper is read; only its charts are not
            stage("figures", f"The figures could not be read: {type(e).__name__}: {e}")
            return None

    def figures_of(self, lib: Library, key: str, req_id: Any) -> dict[str, Any] | None:
        """A read paper's figures, now (a PDF arrived for its XML): progress and a log line, never
        a stage, so the paper's own state is left as it was."""
        from . import figures

        conn = open_store(lib.store_path)
        try:
            emit({"event": "progress", "id": req_id, "op": "figures", "lib": lib.id, "done": 0, "total": 1, "label": "Reading the figures from the PDF beside the XML"})
            out = figures.read_for(conn, key, lib.papers_dir)
            if out is not None:
                emit({"event": "log", "id": req_id, "lib": lib.id, "paper": key, "logger": "figures",
                      "message": f"{out['read']} of {out['plots']} charts read from the PDF beside the XML: {out['values']} values"})
            return out
        except Exception as e:  # noqa: BLE001 — the paper stays read; only its charts are not
            emit({"event": "log", "id": req_id, "lib": lib.id, "paper": key, "logger": "figures", "message": f"{type(e).__name__}: {e}"})
            return None
        finally:
            conn.close()

    def do_figures(self, req: dict[str, Any]) -> None:
        """Every paper's figures read into numbers — a PDF paper's, and an XML paper's from the PDF
        kept beside it — those not read by this reader, or the papers named; `force` reads them again."""
        from . import figures

        lib = self._lib(req)
        req_id = req.get("id")
        conn = open_store(lib.store_path)
        keys = set(req.get("keys") or [])
        totals = {"papers": 0, "figures": 0, "plots": 0, "read": 0, "values": 0}
        try:
            rows = [r for r in conn.execute("SELECT key, file FROM papers WHERE status = 'parsed' AND (format = 'pdf' OR figures_file IS NOT NULL) ORDER BY added_at") if not keys or r["key"] in keys]
            todo = [r for r in rows if req.get("force") or not figures.is_read(conn, r["key"])]
            for i, r in enumerate(todo):
                emit({"event": "progress", "id": req_id, "op": "figures", "lib": lib.id, "done": i, "total": len(todo), "label": f"Reading the charts of paper {i + 1} of {len(todo)}"})
                try:
                    out = figures.read_for(conn, r["key"], lib.papers_dir)
                except Exception as e:  # noqa: BLE001 — one paper's figures do not stop the rest
                    emit({"event": "log", "id": req_id, "lib": lib.id, "paper": r["key"], "logger": "figures", "message": f"{type(e).__name__}: {e}"})
                    continue
                if out is None:
                    continue
                totals["papers"] += 1
                for k in ("figures", "plots", "read", "values"):
                    totals[k] += out[k]
        finally:
            conn.close()
        emit({"event": "done", "id": req_id, "op": "figures", "lib": lib.id, **totals})

    # ---- acquisition, projects, retrieval: the long ones, on the ingest thread ----------

    def do_fetch(self, req: dict[str, Any]) -> None:
        """Candidates into the inbox — the XML where it is open, else an open PDF, else marked
        `needs-pdf` with its links (acquire.py) — then read like any dropped file."""
        lib = self._lib(req)
        req_id = req.get("id")
        ids = [int(i) for i in req.get("ids") or []]
        conn = open_store(lib.store_path)
        try:
            acquire.stage(conn, ids)
            got: list[dict[str, Any]] = []
            for i, cid in enumerate(ids):
                emit({"event": "progress", "id": req_id, "op": "fetch", "lib": lib.id, "done": i, "total": len(ids), "label": f"Fetching {i + 1} of {len(ids)}"})
                got.append(acquire.fetch_one(lib, conn, cid, on_progress=lambda e: emit({**e, "id": req_id, "lib": lib.id})))
        finally:
            conn.close()
        # the XML first, then any PDF fetched beside it for its figures: ingest files the paper as
        # its XML and keeps the PDF for the figures, never the other way round
        paths = [g["path"] for g in got if g.get("path")] + [g["figures"] for g in got if g.get("path") and g.get("figures")]
        conn = open_store(lib.store_path)
        try:
            ids = {g["cand_id"]: dict(conn.execute("SELECT doi, pmid, pmcid FROM candidates WHERE cand_id = ?", (g["cand_id"],)).fetchone() or {}) for g in got if g.get("path")}
        finally:
            conn.close()
        known = {g["path"]: ids.get(g["cand_id"], {}) for g in got if g.get("path")} | {g["figures"]: ids.get(g["cand_id"], {}) for g in got if g.get("path") and g.get("figures")}
        counts: dict[str, int] = {}
        for g in got:
            counts[str(g.get("status"))] = counts.get(str(g.get("status")), 0) + 1
        emit({"event": "stage", "id": req_id, "lib": lib.id, "stage": "fetched", "message": ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "nothing to fetch"})
        filed = self.do_ingest({"id": req_id, "op": "ingest", "lib": lib.id, "paths": paths, "known": known}) if paths else {}
        conn = open_store(lib.store_path)
        try:
            # a file filed is its candidate's paper, whatever identifiers it printed of itself
            for g in got:
                key = filed.get(g.get("path") or "")
                if key:
                    conn.execute("UPDATE candidates SET status = 'ingested', paper_key = ?, error = NULL, updated_at = ? WHERE cand_id = ?", (key, now_iso(), g["cand_id"]))
            conn.commit()
            acquire.reconcile(conn)
            for g in got:
                row = conn.execute("SELECT status, paper_key, error FROM candidates WHERE cand_id = ?", (g["cand_id"],)).fetchone()
                if row:
                    emit({"event": "candidate", "id": req_id, "lib": lib.id, "cand_id": g["cand_id"], "status": row["status"], "paper_key": row["paper_key"], "error": row["error"]})
        finally:
            conn.close()
        emit({"event": "done", "id": req_id, "op": "fetch", "lib": lib.id, "fetched": got})

    def do_merge(self, req: dict[str, Any]) -> None:
        """Several projects' libraries into one (projects.merge), then its rows derived again from
        the saved readings — Docling only for a paper that came without one."""
        req_id = req.get("id")
        emit({"event": "progress", "id": req_id, "op": "merge", "done": 0, "total": 0, "label": "Merging libraries"})
        out = project_rows.merge(self.root, [str(s) for s in req.get("sources") or []], str(req.get("name") or ""), into=req.get("into"))
        target = out["target"]["id"]
        emit({"event": "stage", "id": req_id, "lib": target, "stage": "merged", "message": f"{out['filed']} papers filed, {out['duplicates']} already held, {len(out['keys_to_rebuild'])} to derive again, {len(out['keys_to_parse'])} to read again"})
        rebuilt: list[str] = []
        if out["keys_to_rebuild"]:
            self.do_rebuild({"id": f"{req_id}-rebuild", "op": "rebuild", "lib": target, "keys": out["keys_to_rebuild"]})
            rebuilt = out["keys_to_rebuild"]
        if out["keys_to_parse"]:
            self.do_reparse({"id": f"{req_id}-reparse", "op": "reparse", "lib": target, "keys": out["keys_to_parse"]})
        emit({"event": "done", "id": req_id, "op": "merge", "lib": target, "target": target, "filed": out["filed"], "duplicates": out["duplicates"], "skipped": out["skipped"], "rebuilt": rebuilt, "reparsed": out["keys_to_parse"], "candidates_merged": out["candidates_merged"]})

    def do_embed(self, req: dict[str, Any]) -> None:
        """Every passage of a library embedded once, locally (retrieve.py); a passage already
        embedded under the same recipe is not asked again."""
        from . import retrieve

        lib = self._lib(req)
        req_id = req.get("id")
        conn = open_store(lib.store_path)
        try:
            out = retrieve.embed_library(conn, retrieve.OllamaEmbedder(), on_progress=lambda done, total: emit(
                {"event": "progress", "id": req_id, "op": "embed", "lib": lib.id, "done": done, "total": total, "label": f"Embedding passages: {lib.id}"}))
        finally:
            conn.close()
        emit({"event": "done", "id": req_id, "op": "embed", "lib": lib.id, **out})

    def do_model_label(self, req: dict[str, Any]) -> None:
        """The local model labels the findings a person would be offered (labeller.py), for a
        person to audit; a finding it has labelled is not asked again."""
        from . import labeller, truth

        lib = self._lib(req)
        req_id = req.get("id")
        conn = open_store(lib.store_path)
        try:
            out = labeller.label(conn, n=int(req.get("n") or 100), seed=int(req.get("seed") or 0), per_paper=int(req.get("per_paper") or truth.PER_PAPER),
                                 model=req.get("model") or None, on_progress=lambda done, total, label: emit(
                                     {"event": "progress", "id": req_id, "op": "model_label", "lib": lib.id, "done": done, "total": total, "label": label}))
        finally:
            conn.close()
        emit({"event": "done", "id": req_id, "op": "model_label", "lib": lib.id, **out})

    def answer_async(self, req: dict[str, Any]) -> None:
        """A read that waits on something slow — Europe PMC, the local model, the embedder — on
        its own thread, so the window can go on browsing trees meanwhile."""

        def run() -> None:
            op = req.get("op")
            req_id = req.get("id")
            try:
                if op == "search":
                    lib = self._lib(req)
                    query = str(req.get("query") or "").strip()
                    if not query:
                        raise ValueError("an empty query")
                    got = acquire.search(query, page_size=int(req.get("size") or 25), cursor=str(req.get("cursor") or "*"))
                    conn = open_store(lib.store_path)
                    try:
                        rec = acquire.record_search(lib, conn, query, got["hits"], total=got["total"])
                        acquire.reconcile(conn)
                        by_id = {c["cand_id"]: c for c in acquire.candidates(conn)}
                        hits = []
                        for cid, hit in zip(rec["cand_ids"], got["hits"]):
                            row = by_id.get(cid, {})
                            hits.append({**hit, **row, "cand_id": cid})
                    finally:
                        conn.close()
                    emit({"event": "search", "id": req_id, "lib": lib.id, "query": query, "hits": hits, "total": got["total"], "next_cursor": got["next_cursor"], "added": rec["added"]})
                elif op == "suggest":
                    from .suggest import suggest_queries

                    lib = self._lib(req)
                    m = project_rows.summary(lib)
                    out = suggest_queries(m.get("description") or "", [str(q.get("query") or "") for q in m.get("queries") or []])  # summary() hands every search back as a dict
                    emit({"event": "suggestions", "id": req_id, "lib": lib.id, **out})
                elif op == "query":
                    from . import retrieve

                    lib = self._lib(req)
                    conn = open_store(lib.store_path)
                    try:
                        t = time.time()
                        out = retrieve.query(conn, str(req.get("question") or ""), retrieve.OllamaEmbedder(), k=int(req.get("k") or 8))
                        out["seconds"] = round(time.time() - t, 3)
                    finally:
                        conn.close()
                    emit({"event": "query", "id": req_id, "lib": lib.id, **out})
                else:
                    raise ValueError(f"Unknown op {op!r}")
            except Exception as e:  # noqa: BLE001 — the answer to a failed read is the reason
                emit({"event": "error", "id": req_id, "op": op, "message": str(e)})

        threading.Thread(target=run, daemon=True, name=f"read-{req.get('op')}").start()

    # ---- reads, answered at once -------------------------------------------------------

    def answer(self, req: dict[str, Any]) -> None:
        op = req.get("op")
        req_id = req.get("id")
        try:
            if op == "hello":
                emit({"event": "hello", "id": req_id, "worker": __version__, "root": str(self.root), "python": sys.version.split()[0], "docling": self.docling_version, "device": self._device, "meaning": lanes.active().summary() if lanes.active() else None, "boundary": self._scorer.summary() if self._scorer else None})
            elif op == "libraries":
                emit({"event": "libraries", "id": req_id, "root": str(self.root), "libraries": [l.to_dict() for l in list_libraries(self.root)]})
            elif op == "init":
                lib = create_library(self.root, str(req["name"]), req.get("projectId"))
                conn = open_store(lib.store_path)
                acquire.ensure_schema(conn)
                conn.close()
                if req.get("description"):
                    project_rows.describe(lib, description=str(req["description"]))
                emit({"event": "library", "id": req_id, "library": lib.to_dict()})
            elif op == "projects":
                emit({"event": "projects", "id": req_id, "root": str(self.root), "projects": [project_rows.summary(l) for l in list_libraries(self.root)]})
            elif op == "describe":
                lib = self._lib(req)
                emit({"event": "project", "id": req_id, "project": project_rows.describe(lib, name=req.get("name"), description=req.get("description"))})
            elif op in ("search", "suggest", "query"):
                self.answer_async(req)
            elif op in ("candidates", "wanted", "dismiss", "stage"):
                lib = self._lib(req)
                conn = open_store(lib.store_path)
                try:
                    acquire.reconcile(conn)
                    if op == "candidates":
                        emit({"event": "candidates", "id": req_id, "lib": lib.id, "candidates": acquire.candidates(conn, status=req.get("status"), query=req.get("query"))})
                    elif op == "wanted":
                        from .figures import figures_wanted

                        emit({"event": "wanted", "id": req_id, "lib": lib.id, "candidates": acquire.wanted(conn), "figures": figures_wanted(conn)})
                    else:
                        fn = acquire.dismiss if op == "dismiss" else acquire.stage
                        emit({"event": "dismissed", "id": req_id, "lib": lib.id, "op": op, **fn(conn, [int(i) for i in req.get("ids") or []])})
                finally:
                    conn.close()
            elif op in ("types", "mapping"):
                from . import canonical

                lib = self._lib(req)
                conn = open_store(lib.store_path)
                try:
                    if op == "types":
                        papers_of = [{"key": r["key"], "title": r["title"], "type": r["type"]} for r in conn.execute("SELECT key, title, type FROM papers WHERE status = 'parsed' ORDER BY title")]
                        emit({"event": "types", "id": req_id, "lib": lib.id, "overview": canonical.types_overview(conn), "skeletons": canonical.skeletons(conn), "papers": papers_of})
                    else:
                        key = str(req["key"])
                        emit({"event": "mapping", "id": req_id, "lib": lib.id, "mapping": canonical.mapping(conn, key), "canonical": canonical.canonical_tree(conn, key)})
                finally:
                    conn.close()
            elif op == "retrieval":
                from . import retrieve

                lib = self._lib(req)
                conn = open_store(lib.store_path)
                try:
                    emit({"event": "retrieval", "id": req_id, "lib": lib.id, **retrieve.status(conn, retrieve.OllamaEmbedder())})
                finally:
                    conn.close()
            elif op in ("fetch", "merge", "embed", "model_label", "figures"):
                if op != "merge":
                    self._lib(req)  # fail fast on a bad library
                self.ingest_queue.put(req)
                emit({"event": "queued", "id": req_id, "op": op, "ahead": self.ingest_queue.qsize() - 1})
            elif op in ("ingest", "reparse", "rebuild", "judge"):
                self._lib(req)  # fail fast on a bad library
                self.ingest_queue.put(req)
                emit({"event": "queued", "id": req_id, "op": op, "ahead": self.ingest_queue.qsize() - 1})
            elif op == "papers":
                lib = self._lib(req)
                conn = open_store(lib.store_path)
                emit({"event": "papers", "id": req_id, "lib": lib.id, "papers": list_papers(conn)})
                conn.close()
            elif op == "tree":
                lib = self._lib(req)
                conn = open_store(lib.store_path)
                t = paper_tree(conn, str(req["key"]))
                conn.close()
                if t is None:
                    raise ValueError(f"No paper {req.get('key')!r}")
                emit({"event": "tree", "id": req_id, "lib": lib.id, **t})
            elif op == "node":
                lib = self._lib(req)
                conn = open_store(lib.store_path)
                emit({"event": "node", "id": req_id, "node": node(conn, str(req["node_id"])), "siblings": siblings(conn, str(req["node_id"])) if req.get("siblings") else None})
                conn.close()
            elif op == "section":
                lib = self._lib(req)
                conn = open_store(lib.store_path)
                emit({"event": "section", "id": req_id, "nodes": section(conn, str(req["key"]), str(req["role"]))})
                conn.close()
            elif op == "events":
                lib = self._lib(req)
                conn = open_store(lib.store_path)
                rows = [dict(r) for r in conn.execute("SELECT at, stage, detail FROM events WHERE paper = ? ORDER BY id", (str(req["key"]),))]
                conn.close()
                emit({"event": "events", "id": req_id, "paper": req["key"], "events": rows})
            elif op == "sql":
                lib = self._lib(req)
                conn = open_store(lib.store_path)
                try:
                    emit({"event": "rows", "id": req_id, **run_select(conn, str(req["sql"]), int(req.get("limit", 200)))})
                finally:
                    conn.close()
            elif op == "file":
                lib = self._lib(req)
                conn = open_store(lib.store_path)
                row = conn.execute("SELECT file, format FROM papers WHERE key = ?", (str(req["key"]),)).fetchone()
                conn.close()
                if row is None or not row["file"]:
                    raise ValueError(f"No file for {req.get('key')!r}")
                emit({"event": "file", "id": req_id, "path": str(lib.papers_dir / row["file"]), "format": row["format"], "raw": str(lib.parsed_dir / f"{_safe(str(req['key']))}.docling.json")})
            elif op == "refs":
                lib = self._lib(req)
                conn = open_store(lib.store_path)
                rows = refs_of(conn, str(req["key"]))
                conn.close()
                emit({"event": "refs", "id": req_id, "paper": str(req["key"]), "refs": rows})
            elif op == "edges":
                # a node's edges both ways: what a finding was measured by, what was measured here, what cites a figure
                lib = self._lib(req)
                conn = open_store(lib.store_path)
                both = edges_of(conn, str(req["node_id"]))
                conn.close()
                emit({"event": "edges", "id": req_id, "node_id": str(req["node_id"]), **both})
            elif op == "charts":
                # the numbers read from a figure (figures.py): one figure's plots, or every figure of a paper
                from . import figures

                lib = self._lib(req)
                conn = open_store(lib.store_path)
                try:
                    figures.ensure_schema(conn)
                    if req.get("figure"):
                        out = {"figure": str(req["figure"]), "plots": figures.of_figure(conn, str(req["figure"]))}
                    else:
                        figs = [r[0] for r in conn.execute("SELECT DISTINCT figure FROM charts WHERE paper = ? ORDER BY figure", (str(req.get("key") or ""),))]
                        out = {"key": req.get("key"), "figures": [{"figure": f, "plots": figures.of_figure(conn, f)} for f in figs]}
                finally:
                    conn.close()
                emit({"event": "charts", "id": req_id, "lib": lib.id, **out})
            elif op in ("label_queue", "label", "labels", "truth"):
                # the truth for the finding→method links (truth.py): findings to label, a finding's
                # labels written, every label, and the linker measured against them
                from . import truth

                lib = self._lib(req)
                conn = open_store(lib.store_path)
                try:
                    if op == "label_queue":
                        out = truth.queue(conn, n=int(req.get("n") or 100), seed=int(req.get("seed") or 0),
                                          per_paper=int(req.get("per_paper") or truth.PER_PAPER), finding=req.get("finding") or None)
                        emit({"event": "label_queue", "id": req_id, "lib": lib.id, **out})
                    elif op == "label":
                        saved = truth.save_labels(conn, str(req["finding"]), list(req.get("labels") or []), by=req.get("by"))
                        emit({"event": "labelled", "id": req_id, "lib": lib.id, "finding": str(req["finding"]), "labels": saved})
                    elif op == "labels":
                        emit({"event": "labels", "id": req_id, "lib": lib.id, "labels": truth.labels(conn)})
                    else:
                        emit({"event": "truth", "id": req_id, "lib": lib.id, **truth.report(conn, choose_paragraph=truth._hydration_chooser())})
                finally:
                    conn.close()
            elif op == "audit":
                # Every node against its neighbours — see audit.py. One paper, a library, or a raw document.
                from .audit import audit_doc, summarize

                if req.get("path"):
                    raw_path = Path(str(req["path"]))
                    targets = [(str(req.get("key") or raw_path.name.replace(".docling.json", "")), raw_path)]
                else:
                    lib = self._lib(req)
                    conn = open_store(lib.store_path)
                    keys = [str(req["key"])] if req.get("key") else [r["key"] for r in conn.execute("SELECT key FROM papers ORDER BY added_at, key")]
                    conn.close()
                    targets = [(k, lib.parsed_dir / f"{_safe(k)}.docling.json") for k in keys]
                papers = []
                for key, raw_path in targets:
                    if not raw_path.exists():
                        papers.append({"key": key, "error": "no raw Docling document to audit"})
                        continue
                    tree, findings = audit_doc(json.loads(raw_path.read_text("utf-8")), key)
                    papers.append({"key": key, "title": tree.title, "nodes": sum(1 for _ in tree.walk()), "dropped": tree.dropped, "counts": summarize(findings), "findings": [f.to_dict() for f in findings]})
                emit({"event": "audit", "id": req_id, "papers": papers})
            elif op == "parse_json":
                # A tree from a saved Docling document, no models involved — for tests and inspection.
                doc = json.loads(Path(str(req["path"])).read_text("utf-8"))
                tree = build_tree(doc, str(req.get("key", "doc")))
                emit({"event": "tree", "id": req_id, **tree.to_dict()})
            elif op == "quit":
                if self._layout is not None:
                    self._layout.stop()  # `os._exit` unwinds nothing, so the Docling child would outlive us
                emit({"event": "bye", "id": req_id})
                sys.stdout.flush()
                os._exit(0)
            else:
                raise ValueError(f"Unknown op {op!r}")
        except Exception as e:
            emit({"event": "error", "id": req_id, "op": op, "message": str(e)})


def request_lines():
    """stdin, one line at a time.

    On Windows a thread sitting in a blocking read of the stdin pipe stalls every DLL load in
    the process — numpy's and torch's, on the ingest thread — until the pipe closes. So there
    the pipe is polled with PeekNamedPipe and only what has arrived is read; a console or a
    file (no pipe to peek) falls back to the blocking read, as every other platform uses.
    """
    if sys.platform != "win32":
        yield from sys.stdin
        return
    import ctypes
    import msvcrt

    fd = sys.stdin.fileno()
    handle = msvcrt.get_osfhandle(fd)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    available = ctypes.c_ulong(0)
    if not kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
        yield from sys.stdin  # not a pipe
        return
    buffered = b""
    while True:
        available = ctypes.c_ulong(0)
        if not kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
            break  # the other end closed (ERROR_BROKEN_PIPE)
        if available.value == 0:
            time.sleep(0.02)
            continue
        chunk = os.read(fd, available.value)
        if not chunk:
            break
        buffered += chunk
        while (nl := buffered.find(b"\n")) >= 0:
            line, buffered = buffered[:nl], buffered[nl + 1 :]
            yield line.decode("utf-8", "replace")
    if buffered:
        yield buffered.decode("utf-8", "replace")


_safe = safe_key


def main() -> None:
    root = library_root()
    for a in sys.argv[1:]:
        if a.startswith("--root="):
            root = Path(a.split("=", 1)[1])
    root.mkdir(parents=True, exist_ok=True)
    lanes.configure_from_env(root)  # every question of meaning, verdicts kept beside the libraries
    # The wire is UTF-8 both ways. Windows opens a pipe as the locale code page (cp1252),
    # which cannot carry a Greek letter in a title or a non-ASCII path, and `emit` writes
    # JSON with ensure_ascii=False; reconfigure rather than depend on PYTHONUTF8.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    w = Worker(root)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    docling_log = logging.getLogger("docling")
    docling_log.setLevel(logging.INFO)
    docling_log.addHandler(_LogForwarder(w))
    docling_log.propagate = False  # once, as an event; not again on stderr
    threading.Thread(target=w.ingest_loop, daemon=True, name="ingest").start()
    emit({"event": "ready", "worker": __version__, "root": str(root)})
    for line in request_lines():
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            emit({"event": "error", "message": f"not JSON: {e}"})
            continue
        w.answer(req)
    emit({"event": "bye"})


if __name__ == "__main__":
    main()
