"""Docling's layout stage, in a child process the worker can lose.

`litrag-parser` exits `3221225477` (0xC0000005, an access violation) part-way through a run of
PDFs. It is intermittent, it is not a poison file — every paper that killed it parsed fine on a
later attempt — and it costs far more than the paper it lands on: the process dies mid-run, and
every paper still queued is simply never read (BACKLOG.md, and seventeen PDFs of held-out 4).

Nothing catches a native crash inside the process it happens in, so the only remedy is to put it
somewhere the worker can survive. The seam is already in the design: the raw Docling document is
written to `parsed/<key>.docling.json` before any row, and everything after it is pure Python
over that dict. So the child's whole job is to produce that file, and the parent's job is to
supervise it: a per-paper timeout, a respawn when it dies, one retry in a fresh child, and then
the paper is quarantined with a reason rather than taking the run down with it.

The child is long-lived and reads many papers, because Docling's converter takes seconds to
build and pulls torch: spawning one per paper would cost more than the crash does. It answers
one JSON line per request, and writes the document itself rather than piping megabytes back.

    python -m litrag_parser.layout --serve           the child
    LITRAG_LAYOUT_CHILD=off                          convert in-process, as before
"""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

#: How long one paper may take before the child is assumed wedged. A deadlock guard, not a
#: performance bound — but not so loose that the guard costs more than the fault. Measured over
#: the campaign's 336-paper ingest on the 5080: about 4.6 s a paper, and the slowest well under
#: a minute. At the first cut of 600 s a wedge cost twenty minutes, because it is paid twice —
#: once on the attempt and once on the retry. 300 s is sixty times the typical paper and brings
#: that to ten.
TIMEOUT = float(os.environ.get("LITRAG_LAYOUT_TIMEOUT", "300"))


def enabled() -> bool:
    return os.environ.get("LITRAG_LAYOUT_CHILD", "on").lower() not in ("off", "0", "false", "no")


class LayoutCrashed(RuntimeError):
    """The child died reading this paper — twice, in two fresh processes."""


class LayoutTimedOut(RuntimeError):
    """The child took longer than `TIMEOUT` on one paper and was killed."""


# ---- the child ----------------------------------------------------------------------------


def _convert(converter: Any, path: Path) -> dict[str, Any]:
    from .worker import jats_stream

    source = jats_stream(path) if path.suffix.lower() == ".xml" else str(path)
    result = converter.convert(source, raises_on_error=True)
    status = getattr(result, "status", None)
    if status is not None and str(status.value if hasattr(status, "value") else status) not in ("success", "partial_success"):
        raise RuntimeError(f"Docling returned {status}")
    doc = result.document.export_to_dict()
    if not doc.get("texts") and not doc.get("tables"):
        raise RuntimeError("Docling read nothing from the file: no text, no tables — the file is not a paper, or its XML defeats the backend")
    return doc


def serve() -> int:
    """One request per line on stdin, one answer per line on stdout.

    Docling logs, and any library that prints, would corrupt the wire — so the answers go out on
    a private duplicate of the real stdout and `sys.stdout` is pointed at stderr, which is the
    same guard the worker's own wire wants (BACKLOG.md)."""
    wire = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    sys.stdout = sys.stderr

    def answer(msg: dict[str, Any]) -> None:
        wire.write(json.dumps(msg, default=str) + "\n")
        wire.flush()

    converter: Any = None
    answer({"ready": True, "pid": os.getpid()})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if req.get("op") == "quit":
            break
        started = time.time()
        try:
            if converter is None:
                from .worker import Worker

                converter = Worker(Path(req.get("root") or ".")).converter(None)
            doc = _convert(converter, Path(req["path"]))
            # written aside and renamed: a kill on the timeout path must not leave half a
            # document behind, because everything downstream tests only that the file exists
            out = Path(req["out"])
            tmp = out.with_suffix(out.suffix + f".part{os.getpid()}")
            tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, out)
            answer({"ok": True, "out": req["out"], "seconds": round(time.time() - started, 2),
                    "texts": len(doc.get("texts", [])), "tables": len(doc.get("tables", [])),
                    "pictures": len(doc.get("pictures", []))})
        except Exception as e:  # noqa: BLE001 — a Python failure is the paper's, not the child's
            answer({"ok": False, "error": f"{type(e).__name__}: {e}", "seconds": round(time.time() - started, 2)})
    return 0


# ---- the parent ---------------------------------------------------------------------------


class LayoutChild:
    """A Docling child process, respawned whenever it dies.

    Not thread-safe: the worker runs one ingest at a time on one thread, and that is the only
    caller."""

    def __init__(self, root: Path, on_log: Callable[[str], None] | None = None,
                 command: list[str] | None = None):
        self.root = root
        #: called per line of the child's stderr. A plain attribute rather than a closure held
        #: from the first paper, so it can be repointed as papers go by — the first version
        #: captured one paper's id for the life of the child and filed every later paper's
        #: Docling log under it.
        self.on_log = on_log
        #: the child's command line. The tests give a stub that crashes, hangs or refuses on
        #: demand, so the supervision can be measured without Docling and without waiting for a
        #: real access violation, which is intermittent and would never make a test.
        self.command = command or [sys.executable, "-m", "litrag_parser.layout", "--serve"]
        self.proc: subprocess.Popen[str] | None = None
        self.spawns = 0
        # a multi-gigabyte torch child holding GPU memory must not outlive its parent. `stop()`
        # runs on the `quit` op, but a worker can die without one — which is the premise of this
        # whole module — and the window kills it 1.5 s after asking it to quit.
        atexit.register(self.stop)

    @staticmethod
    def _reap(proc: subprocess.Popen[str]) -> int | None:
        """Kill a child and wait for it, so nothing is left behind.

        `kill()` without `wait()` leaves a zombie and holds the three pipes and the drain thread
        until the collector reaches the abandoned object — and this class exists to stop a
        process outliving its purpose, so it cannot do that itself."""
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 — already gone
            pass
        try:
            return proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            return proc.poll()

    def _line(self, proc: subprocess.Popen[str], timeout: float,
              heartbeat: Callable[[], None] | None, what: str) -> str:
        """One line from the child, within `timeout`, beating while it waits.

        Every read of the child's pipe goes through here. The first version read the child's
        `ready` line with a bare blocking `readline()` outside any deadline, so a child that
        hung while importing torch — or while fetching Docling's models on a first run — froze
        the worker with no heartbeat: exactly the failure this module exists to remove."""
        assert proc.stdout is not None
        box: dict[str, str] = {}

        def read() -> None:
            assert proc.stdout is not None
            box["line"] = proc.stdout.readline()

        th = threading.Thread(target=read, daemon=True)
        th.start()
        deadline = time.time() + timeout
        while th.is_alive() and time.time() < deadline:
            th.join(1.5)
            if th.is_alive() and heartbeat:
                heartbeat()
        if th.is_alive():
            code = self._reap(proc)
            if self.proc is proc:
                self.proc = None
            raise LayoutTimedOut(f"the layout child took more than {timeout:g}s {what} (killed, exit {code})")
        return box.get("line") or ""

    def _spawn(self, timeout: float, heartbeat: Callable[[], None] | None) -> subprocess.Popen[str]:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
        proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=env, cwd=str(Path(__file__).resolve().parent.parent),
        )
        self.spawns += 1
        threading.Thread(target=self._drain, args=(proc,), daemon=True).start()
        hello = self._line(proc, timeout, heartbeat, "starting up")
        if not hello.strip():
            raise LayoutCrashed(f"the layout child exited {self._reap(proc)} before it was ready")
        return proc

    def _drain(self, proc: subprocess.Popen[str]) -> None:
        """Docling's own log lines, so they are not lost and the pipe never fills."""
        if proc.stderr is None:
            return
        for line in proc.stderr:
            if self.on_log:
                self.on_log(line.rstrip())

    def ensure(self, timeout: float = TIMEOUT,
               heartbeat: Callable[[], None] | None = None) -> subprocess.Popen[str]:
        if self.proc is None or self.proc.poll() is not None:
            self.proc = self._spawn(timeout, heartbeat)
        return self.proc

    def stop(self) -> None:
        """Ask the child to go, and make sure it has. Safe to call more than once."""
        proc, self.proc = self.proc, None
        if proc is None:
            return
        if proc.poll() is not None:
            proc.wait()  # reap it even when it is already dead
            return
        try:
            if proc.stdin:
                proc.stdin.write(json.dumps({"op": "quit"}) + "\n")
                proc.stdin.flush()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self._reap(proc)

    def _ask(self, path: Path, out: Path, timeout: float, heartbeat: Callable[[], None] | None) -> dict[str, Any]:
        proc = self.ensure(timeout, heartbeat)
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"path": str(path), "out": str(out), "root": str(self.root)}) + "\n")
        proc.stdin.flush()
        line = self._line(proc, timeout, heartbeat, f"on {path.name}")
        if not line.strip():  # the child died: an empty read is how a native crash arrives here
            try:
                code = proc.wait(timeout=5)  # not `poll`: the pipe closes before the process is reaped
            except subprocess.TimeoutExpired:
                code = self._reap(proc)
            if self.proc is proc:
                self.proc = None
            raise LayoutCrashed(f"the layout child exited {code} reading {path.name}")
        return json.loads(line)

    def convert(self, path: Path, out: Path, *, timeout: float | None = None,
                heartbeat: Callable[[], None] | None = None,
                on_retry: Callable[[str], None] | None = None) -> dict[str, Any]:
        """The raw Docling document for one paper, written to `out`.

        A crash costs this paper one retry in a fresh child, because every paper that has ever
        killed the worker parsed fine on a later attempt. A second crash is the paper's own, and
        is raised so the caller can quarantine it with a reason."""
        timeout = TIMEOUT if timeout is None else timeout
        try:
            answer = self._ask(path, out, timeout, heartbeat)
        except (LayoutCrashed, LayoutTimedOut) as first:
            if on_retry:
                on_retry(str(first))
            try:
                answer = self._ask(path, out, timeout, heartbeat)
            except (LayoutCrashed, LayoutTimedOut) as second:
                raise LayoutCrashed(f"{first}; and again in a fresh child: {second}") from second
        if not answer.get("ok"):
            raise RuntimeError(answer.get("error") or "the layout child gave no reason")
        return answer


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--serve" in args:
        return serve()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
