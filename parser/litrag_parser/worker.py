"""The parsing worker: JSON lines in, JSON lines out.

The desktop app spawns one of these and talks to it over stdio. Every request
is one line — `{"id": ..., "op": ..., ...}` — and every answer is one or more
lines carrying the same id: `stage` and `working` events while a paper is
being read, `paper`/`tree` events as it lands, and a final `done` (or
`error`). Reads (`papers`, `tree`, `node`, `sql`) are answered at once from
the main thread; `ingest`, `reparse` and `rebuild` run one at a time on the
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

from . import __version__, boundary, lanes
from .library import Library, create_library, library_root, list_libraries, now_iso, open_library, parsed_papers, safe_key
from .citations import link_citations
from .edges import link_edges, summarize as summarize_edges
from .paper_type import decide as decide_type, lookup_types
from .harness import pdf_title
from .judge import Judge, available as judge_available, default_model as judge_model
from .recover import recover_from_pdf
from .citations import summarize as summarize_citations
from .store import (cited_by, cites_of, edges_of, file_paper, list_papers, log_event, node, open_store, paper_tree, refs_of, run_select, save_edges, save_refs, save_tree, section, set_pub_types, set_status, set_type, sha256_of, siblings)
from .tree import build_tree

_out_lock = threading.Lock()


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


def sniff_ids(path: Path) -> tuple[str | None, str | None]:
    """The DOI and PMCID on a PDF's first pages, if printed there."""
    if path.suffix.lower() != ".pdf":
        return None, None
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        text = ""
        for i in range(min(2, len(pdf))):
            text += pdf[i].get_textpage().get_text_range() + "\n"
        pdf.close()
    except Exception:  # a PDF pdfium cannot open still gets a hash key
        return None, None
    # the first DOI whose suffix carries a digit: "10.1073/pnas" at a line's end is the prefix of one, not a DOI
    doi = next((m for m in _DOI.finditer(text) if any(ch.isdigit() for ch in m.group(0).split("/", 1)[1])), None)
    pmc = _PMCID.search(text)
    return (doi.group(0).rstrip(".,;:") if doi else None), (pmc.group(0) if pmc else None)


def guess_title(path: Path) -> str | None:
    """A PDF's title: its Title metadata when that is a title, else the largest line of
    text on the first page, when that is a title — four words or more, not furniture."""
    from .harness import pdf_title

    title = pdf_title(path)
    if title:
        return title
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        if not len(pdf):
            pdf.close()
            return None
        tp = pdf[0].get_textpage()
        n = tp.count_chars()
        text = tp.get_text_range(0, n)
        rows: list[tuple[str, list[float]]] = []
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
        pdf.close()
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
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={query}&format=json&resultType=lite&pageSize=5"
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
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        n = len(pdf)
        pdf.close()
        return n
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
            conn.execute("DELETE FROM events WHERE paper = ? AND stage LIKE 'lane-%'", (key,))
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
            except Exception as e:  # never let one paper kill the worker
                emit({"event": "error", "id": req.get("id"), "message": str(e), "trace": traceback.format_exc()})
            finally:
                self.current = {}

    def _lib(self, req: dict[str, Any]) -> Library:
        lib = open_library(self.root, str(req.get("lib", "")))
        if lib is None:
            raise ValueError(f"No library {req.get('lib')!r} under {self.root}")
        lib.ensure_dirs()
        return lib

    def do_ingest(self, req: dict[str, Any]) -> None:
        lib = self._lib(req)
        req_id = req.get("id")
        conn = open_store(lib.store_path)
        filed: list[tuple[str, Path]] = []
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
                if not doi and not pmcid and not req.get("offline"):
                    doi, pmid = lookup_by_title(guess_title(src))  # Europe PMC, by the paper's own title; nothing when offline or unsure
                    if doi or pmid:
                        emit({"event": "identified", "id": req_id, "path": str(src), "doi": doi, "pmid": pmid})
            result = file_paper(conn, title=src.stem, file="", sha256=sha, fmt=fmt, doi=doi, pmid=pmid, pmcid=pmcid, now=now_iso())
            row = conn.execute("SELECT status, file FROM papers WHERE key = ?", (result.key,)).fetchone()
            # A paper already read stays as it was read — the same DOI arriving as a second file
            # (a PDF after its JATS, say) is noted, not swapped in. `reread` is the way to replace it.
            already = bool(result.existed and row and row["status"] == "parsed" and row["file"] and not req.get("reread"))
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
            if not already and not req.get("offline") and (doi or pmid):
                types = lookup_types(doi, pmid)  # Europe PMC's word on what kind of paper it is, kept with the paper
                if types:
                    set_pub_types(conn, result.key, types)
            log_event(conn, result.key, now_iso(), "filed", f"{'seen before, kept' if already else 'seen before' if result.existed else 'new'}: {src.name}")
            emit({"event": "paper", "id": req_id, "paper": result.key, "existed": result.existed, "kept": already, "doi": doi, "pmcid": pmcid, "file": dest.name, "status": "parsed" if already else "queued", "pages": page_count(dest)})
            if not already:
                filed.append((result.key, dest))
        conn.close()
        for key, path in filed:
            self.parse_one(lib, key, path, req_id, ask_judge=bool(req.get("judge")))
        emit({"event": "done", "id": req_id, "op": "ingest", "parsed": [k for k, _ in filed]})

    def do_reparse(self, req: dict[str, Any]) -> None:
        lib = self._lib(req)
        conn = open_store(lib.store_path)
        keys = req.get("keys") or [r["key"] for r in conn.execute("SELECT key FROM papers WHERE file IS NOT NULL")]
        files = {r["key"]: r["file"] for r in conn.execute("SELECT key, file FROM papers")}
        conn.close()
        for key in keys:
            if key in files and files[key]:
                self.parse_one(lib, key, lib.papers_dir / files[key], req.get("id"), ask_judge=bool(req.get("judge")))
        emit({"event": "done", "id": req.get("id"), "op": "reparse", "parsed": keys})

    def do_rebuild(self, req: dict[str, Any]) -> None:
        """Rows again from the raw parses, without Docling — the tree builder changed."""
        lib = self._lib(req)
        conn = open_store(lib.store_path)
        rebuilt = []
        keys = set(req.get("keys") or [])
        for row in parsed_papers(lib.dir, sorted(keys) or None):
            key = row["key"]
            source = row["source"]
            hint = pdf_title(source) if row["format"] == "pdf" and source and source.exists() else None
            ask = bool(req.get("judge"))
            judge = Judge(conn, key, ask_model=ask and judge_available(), model=judge_model(lib.dir), scorer=self.scorer() if ask else None)
            doc = json.loads(row["raw"].read_text("utf-8"))
            recover_from_pdf(doc, source if row["format"] == "pdf" else None)
            tree = build_tree(doc, key, title_hint=hint, judge=judge)
            n = save_tree(conn, key, tree, parser=f"{'judge ' + judge.model if ask else 'rebuild'} {__version__}", parsed_at=now_iso(), seconds=0.0)
            xml = source.read_bytes() if row["format"] == "jats" and source and source.exists() else None
            refs, cites = link_citations(tree, xml)
            save_refs(conn, key, refs, cites)
            edges = link_edges(tree, key, lanes.active())
            save_edges(conn, key, edges)
            kind = decide_type(tree, jats_xml=xml, pub_types=row["pub_types"], oracle=lanes.active())
            set_type(conn, key, kind["type"], kind["source"], kind["detail"])
            self._note_events(conn, key, tree)
            emit({"event": "tree", "id": req.get("id"), "paper": key, "title": tree.title, "type": kind, "roles": tree.roles, "has_methods": tree.has_methods, "nodes": n, "pages": len(tree.pages), "dropped": tree.dropped, "repairs": tree.repairs, "notes": len(tree.notes), "judged": judge.summary(), "meaning": self._meaning(), "edges": summarize_edges(tree, edges), **summarize_citations(refs, cites)})
            rebuilt.append(key)
        conn.close()
        emit({"event": "done", "id": req.get("id"), "op": req.get("op", "rebuild"), "rebuilt": rebuilt})

    def parse_one(self, lib: Library, key: str, path: Path, req_id: Any, ask_judge: bool = False) -> None:
        self.current = {"id": req_id, "paper": key}
        conn = open_store(lib.store_path)
        started = time.time()

        def stage(name: str, message: str, **extra: Any) -> None:
            log_event(conn, key, now_iso(), name, message)
            emit({"event": "stage", "id": req_id, "paper": key, "stage": name, "message": message, "elapsed": round(time.time() - started, 1), **extra})

        try:
            set_status(conn, key, "parsing")
            stage("opening", f"Opening {path.name}", pages=page_count(path))
            converter = self.converter(req_id)
            self.current = {"id": req_id, "paper": key}
            stage("layout", "Reading the layout: headings, paragraphs, tables, figures")
            result_box: dict[str, Any] = {}

            def run() -> None:
                try:
                    source = jats_stream(path) if path.suffix.lower() == ".xml" else str(path)
                    result_box["result"] = converter.convert(source, raises_on_error=True)
                except Exception as e:  # reported below on the main path
                    result_box["error"] = e

            th = threading.Thread(target=run, daemon=True)
            th.start()
            while th.is_alive():
                th.join(1.5)
                if th.is_alive():
                    emit({"event": "working", "id": req_id, "paper": key, "stage": "layout", "elapsed": round(time.time() - started, 1)})
            if "error" in result_box:
                raise result_box["error"]
            result = result_box["result"]
            status = getattr(result, "status", None)
            if status is not None and str(status.value if hasattr(status, "value") else status) not in ("success", "partial_success"):
                raise RuntimeError(f"Docling returned {status}")
            doc = result.document.export_to_dict()
            if not doc.get("texts") and not doc.get("tables"):
                raise RuntimeError("Docling read nothing from the file: no text, no tables — the file is not a paper, or its XML defeats the backend")
            raw_path = lib.parsed_dir / f"{_safe(key)}.docling.json"
            raw_path.write_text(json.dumps(doc, ensure_ascii=False), "utf-8")
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
            tree = build_tree(doc, key, title_hint=pdf_title(path) if path.suffix.lower() == ".pdf" else None, judge=judge)
            seconds = round(time.time() - started, 1)
            n = save_tree(conn, key, tree, parser=f"docling {self.docling_version} · litrag-parser {__version__}", parsed_at=now_iso(), seconds=seconds)
            xml = path.read_bytes() if path.suffix.lower() == ".xml" else None
            refs, cites = link_citations(tree, xml)
            save_refs(conn, key, refs, cites)
            edges = link_edges(tree, key, lanes.active())
            save_edges(conn, key, edges)
            linked = summarize_edges(tree, edges)
            stored = conn.execute("SELECT pub_types FROM papers WHERE key = ?", (key,)).fetchone()
            kind = decide_type(tree, jats_xml=xml, pub_types=stored["pub_types"] if stored else None, oracle=lanes.active())
            set_type(conn, key, kind["type"], kind["source"], kind["detail"])
            self._note_events(conn, key, tree)
            o = lanes.active()
            if o is not None and o.summary()["down"] and not self._reported_down:
                self._reported_down = True
                stage("meaning", f"The embedder is not answering ({o.summary()['error']}): texts the vocabulary does not know read as `other` and are not stored; a rebuild once it answers will read them")
            links = summarize_citations(refs, cites)
            judged = judge.summary()
            stage("saved", f"{n} nodes, {links['refs']} references, {links['citations']} citation links, {linked['linked']} of {linked['findings']} findings linked to a method, a {kind['type']} paper by its {kind['source']}" + (f", {judged['joined']} of {judged['asked']} judged pairs joined" if judged["asked"] else "") + f" in {seconds}s", nodes=n, **links, judged=judged, edges=linked, type=kind)
            emit({"event": "tree", "id": req_id, "paper": key, "title": tree.title, "type": kind, "roles": tree.roles, "has_methods": tree.has_methods, "nodes": n, "pages": len(tree.pages), "seconds": seconds, "raw": str(raw_path), "dropped": tree.dropped, "repairs": tree.repairs, "notes": len(tree.notes), "judged": judged, "meaning": self._meaning(), "edges": linked, **links})
        except Exception as e:
            set_status(conn, key, "failed", error=str(e))
            log_event(conn, key, now_iso(), "failed", str(e))
            emit({"event": "stage", "id": req_id, "paper": key, "stage": "failed", "message": str(e), "trace": traceback.format_exc(), "elapsed": round(time.time() - started, 1)})
        finally:
            conn.close()

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
                open_store(lib.store_path).close()
                emit({"event": "library", "id": req_id, "library": lib.to_dict()})
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
