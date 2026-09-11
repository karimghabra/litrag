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

from . import __version__
from .library import Library, create_library, library_root, list_libraries, now_iso, open_library
from .store import (file_paper, list_papers, log_event, node, open_store, paper_tree, run_select, save_tree, section, set_status, sha256_of, siblings)
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

    raw = path.read_bytes()
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
    doi = _DOI.search(text)
    pmc = _PMCID.search(text)
    return (doi.group(0).rstrip(".,;:") if doi else None), (pmc.group(0) if pmc else None)


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
            log_event(conn, result.key, now_iso(), "filed", f"{'seen before, kept' if already else 'seen before' if result.existed else 'new'}: {src.name}")
            emit({"event": "paper", "id": req_id, "paper": result.key, "existed": result.existed, "kept": already, "doi": doi, "pmcid": pmcid, "file": dest.name, "status": "parsed" if already else "queued", "pages": page_count(dest)})
            if not already:
                filed.append((result.key, dest))
        conn.close()
        for key, path in filed:
            self.parse_one(lib, key, path, req_id)
        emit({"event": "done", "id": req_id, "op": "ingest", "parsed": [k for k, _ in filed]})

    def do_reparse(self, req: dict[str, Any]) -> None:
        lib = self._lib(req)
        conn = open_store(lib.store_path)
        keys = req.get("keys") or [r["key"] for r in conn.execute("SELECT key FROM papers WHERE file IS NOT NULL")]
        files = {r["key"]: r["file"] for r in conn.execute("SELECT key, file FROM papers")}
        conn.close()
        for key in keys:
            if key in files and files[key]:
                self.parse_one(lib, key, lib.papers_dir / files[key], req.get("id"))
        emit({"event": "done", "id": req.get("id"), "op": "reparse", "parsed": keys})

    def do_rebuild(self, req: dict[str, Any]) -> None:
        """Rows again from the raw parses, without Docling — the tree builder changed."""
        lib = self._lib(req)
        conn = open_store(lib.store_path)
        rebuilt = []
        for key in [r["key"] for r in conn.execute("SELECT key FROM papers ORDER BY added_at, key")]:
            raw = lib.parsed_dir / f"{_safe(key)}.docling.json"
            if not raw.exists():
                continue
            tree = build_tree(json.loads(raw.read_text("utf-8")), key)
            n = save_tree(conn, key, tree, parser=f"rebuild {__version__}", parsed_at=now_iso(), seconds=0.0)
            emit({"event": "tree", "id": req.get("id"), "paper": key, "title": tree.title, "roles": tree.roles, "has_methods": tree.has_methods, "nodes": n, "pages": len(tree.pages)})
            rebuilt.append(key)
        conn.close()
        emit({"event": "done", "id": req.get("id"), "op": "rebuild", "rebuilt": rebuilt})

    def parse_one(self, lib: Library, key: str, path: Path, req_id: Any) -> None:
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
            raw_path = lib.parsed_dir / f"{_safe(key)}.docling.json"
            raw_path.write_text(json.dumps(doc, ensure_ascii=False), "utf-8")
            stage("tree", "Building the tree and assigning facets", texts=len(doc.get("texts", [])), tables=len(doc.get("tables", [])), pictures=len(doc.get("pictures", [])))
            tree = build_tree(doc, key)
            seconds = round(time.time() - started, 1)
            n = save_tree(conn, key, tree, parser=f"docling {self.docling_version} · litrag-parser {__version__}", parsed_at=now_iso(), seconds=seconds)
            stage("saved", f"{n} nodes in {seconds}s", nodes=n)
            emit({"event": "tree", "id": req_id, "paper": key, "title": tree.title, "roles": tree.roles, "has_methods": tree.has_methods, "nodes": n, "pages": len(tree.pages), "seconds": seconds, "raw": str(raw_path)})
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
                emit({"event": "hello", "id": req_id, "worker": __version__, "root": str(self.root), "python": sys.version.split()[0], "docling": self.docling_version, "device": self._device})
            elif op == "libraries":
                emit({"event": "libraries", "id": req_id, "root": str(self.root), "libraries": [l.to_dict() for l in list_libraries(self.root)]})
            elif op == "init":
                lib = create_library(self.root, str(req["name"]), req.get("projectId"))
                open_store(lib.store_path).close()
                emit({"event": "library", "id": req_id, "library": lib.to_dict()})
            elif op in ("ingest", "reparse", "rebuild"):
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


def _safe(key: str) -> str:
    """A paper key as a file name: `doi:10.1/abc` → `doi_10.1_abc`."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key)


def main() -> None:
    root = library_root()
    for a in sys.argv[1:]:
        if a.startswith("--root="):
            root = Path(a.split("=", 1)[1])
    root.mkdir(parents=True, exist_ok=True)
    w = Worker(root)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    docling_log = logging.getLogger("docling")
    docling_log.setLevel(logging.INFO)
    docling_log.addHandler(_LogForwarder(w))
    docling_log.propagate = False  # once, as an event; not again on stderr
    threading.Thread(target=w.ingest_loop, daemon=True, name="ingest").start()
    emit({"event": "ready", "worker": __version__, "root": str(root)})
    for line in sys.stdin:
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
