"""A stand-in for the layout child, which fails on demand.

The real child dies of an intermittent native access violation inside Docling — which cannot be
summoned in a test, and which needs the models. This speaks the same one-line protocol and fails
in each of the ways that matter, so `LayoutChild`'s supervision can be measured instead of
hoped for. `LITRAG_FAKE_LAYOUT` says how it should behave:

    ok            answer every request
    crash:N       exit abruptly on request N (1-based) — a native crash, no Python exception
    hang:N        never answer request N
    never-ready   hang before saying it is up
    die-before-ready  exit before saying it is up
    error:N       answer request N with a refusal, the way a Python failure in Docling arrives
    crash-always  exit abruptly on every request, in every fresh process
"""
import json
import os
import sys
import time


def main() -> int:
    mode = os.environ.get("LITRAG_FAKE_LAYOUT", "ok")
    # a count that survives respawning, so "crash on the first request of every child" and
    # "crash once, then succeed" can be told apart
    counter = os.environ.get("LITRAG_FAKE_COUNTER", "")
    kind, _, which = mode.partition(":")
    which = int(which) if which else 1

    wire = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    sys.stdout = sys.stderr

    def answer(msg: dict) -> None:
        wire.write(json.dumps(msg) + "\n")
        wire.flush()

    if kind == "never-ready":
        # a child that hangs before it says it is up: importing torch, or fetching Docling's
        # models on a first run. The first version read this line with no deadline at all.
        while True:
            time.sleep(3600)
    if kind == "die-before-ready":
        os._exit(7)
    answer({"ready": True, "pid": os.getpid()})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        if req.get("op") == "quit":
            break
        n = 1
        if counter:
            try:
                n = int(open(counter).read().strip() or "0") + 1
            except OSError:
                n = 1
            with open(counter, "w") as f:
                f.write(str(n))
        if kind == "crash-always" or (kind == "crash" and n == which):
            # A real access violation, not a simulated exit, so what is tested is the actual
            # failure: a process that vanishes with no Python exception and no chance to say
            # anything. `ctypes.string_at(0)` will not do — it raises a catchable OSError.
            # Reading through a bad pointer is not caught by anything and exits 3221225477
            # (0xC0000005) on Windows, which is the code the worker dies with.
            import ctypes

            ctypes.cast(1, ctypes.POINTER(ctypes.c_int))[0]
            os._exit(1)  # unreachable on any platform that has memory protection
        if kind == "hang" and n == which:
            while True:
                time.sleep(3600)
        if kind == "error" and n == which:
            answer({"ok": False, "error": "RuntimeError: Docling read nothing from the file"})
            continue
        with open(req["out"], "w", encoding="utf-8") as f:
            json.dump({"texts": [{"text": "a paragraph"}], "tables": [], "pictures": []}, f)
        answer({"ok": True, "out": req["out"], "seconds": 0.0, "texts": 1, "tables": 0, "pictures": 0})
    return 0


if __name__ == "__main__":
    sys.exit(main())
