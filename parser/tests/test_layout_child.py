"""Fault injection on the layout child: a crash costs one paper, once.

The real failure is an intermittent native access violation inside Docling, which cannot be
summoned on demand and needs the models. `fake_layout.py` speaks the same protocol and fails in
each of the ways that matter — an abrupt exit, a wedge, a refusal — so what is measured here is
the supervision, which is the part that was written.
"""

import json
import sys
import time
from pathlib import Path

import pytest

from litrag_parser.layout import LayoutChild, LayoutCrashed, LayoutTimedOut, enabled

FAKE = [sys.executable, str(Path(__file__).parent / "fake_layout.py")]


def _child(tmp_path: Path, monkeypatch, mode: str, counter: bool = True) -> LayoutChild:
    monkeypatch.setenv("LITRAG_FAKE_LAYOUT", mode)
    if counter:
        monkeypatch.setenv("LITRAG_FAKE_COUNTER", str(tmp_path / "n.txt"))
    return LayoutChild(tmp_path, command=FAKE)


def test_a_paper_is_read_and_the_document_is_written(tmp_path, monkeypatch):
    child = _child(tmp_path, monkeypatch, "ok")
    try:
        out = tmp_path / "a.docling.json"
        answer = child.convert(tmp_path / "a.pdf", out)
        assert answer["ok"] and out.exists()
        assert child.spawns == 1
        child.convert(tmp_path / "b.pdf", tmp_path / "b.docling.json")
        assert child.spawns == 1  # the same child reads the next paper: Docling is not reloaded
    finally:
        child.stop()


def test_a_crash_costs_one_paper_one_retry_and_the_worker_lives(tmp_path, monkeypatch):
    """Every paper that has ever killed the worker parsed fine on a later attempt, so a crash
    buys one retry in a fresh process rather than failing the paper."""
    child = _child(tmp_path, monkeypatch, "crash:1")
    retries: list[str] = []
    try:
        out = tmp_path / "a.docling.json"
        answer = child.convert(tmp_path / "a.pdf", out, on_retry=retries.append)
        assert answer["ok"] and out.exists()
        assert child.spawns == 2  # the dead child was replaced
        # the reason names the code the process died with, because "the child died" without it
        # is not a diagnosis: 3221225477 (0xC0000005) on Windows, -11 (SIGSEGV) on POSIX
        assert len(retries) == 1, retries
        assert ("3221225477" in retries[0]) or ("-11" in retries[0]), retries
    finally:
        child.stop()


def test_a_paper_that_crashes_twice_is_raised_rather_than_taking_the_run_down(tmp_path, monkeypatch):
    child = _child(tmp_path, monkeypatch, "crash-always", counter=False)
    try:
        with pytest.raises(LayoutCrashed) as e:
            child.convert(tmp_path / "a.pdf", tmp_path / "a.docling.json")
        assert "again in a fresh child" in str(e.value)  # both attempts are in the reason
        assert child.spawns == 2
    finally:
        child.stop()


def test_a_wedged_child_is_killed_and_the_paper_retried(tmp_path, monkeypatch):
    child = _child(tmp_path, monkeypatch, "hang:1")
    try:
        answer = child.convert(tmp_path / "a.pdf", tmp_path / "a.docling.json", timeout=2)
        assert answer["ok"]  # the second attempt, in a fresh child, answered
        assert child.spawns == 2
    finally:
        child.stop()


def test_a_child_that_hangs_on_every_paper_gives_up_with_a_reason(tmp_path, monkeypatch):
    child = _child(tmp_path, monkeypatch, "hang:0", counter=False)  # n is always 1 without a counter
    monkeypatch.setenv("LITRAG_FAKE_LAYOUT", "hang:1")
    try:
        with pytest.raises((LayoutCrashed, LayoutTimedOut)) as e:
            child.convert(tmp_path / "a.pdf", tmp_path / "a.docling.json", timeout=2)
        assert "took more than" in str(e.value)
    finally:
        child.stop()


def test_a_refusal_is_the_papers_own_failure_and_is_not_retried(tmp_path, monkeypatch):
    """A Python error inside Docling — an XML its backend cannot read — is about the file, not
    the process. Retrying it would only cost time and say the same thing."""
    child = _child(tmp_path, monkeypatch, "error:1")
    try:
        with pytest.raises(RuntimeError) as e:
            child.convert(tmp_path / "a.pdf", tmp_path / "a.docling.json")
        assert "Docling read nothing" in str(e.value)
        assert child.spawns == 1  # no respawn: the child is healthy
    finally:
        child.stop()


def test_the_heartbeat_is_called_while_a_paper_is_being_read(tmp_path, monkeypatch):
    """The first attempt wedges and is killed at the timeout; the retry answers. What is under
    test is that the window heard something in between rather than sitting on a spinner."""
    beats: list[int] = []
    child = _child(tmp_path, monkeypatch, "hang:1")
    try:
        answer = child.convert(tmp_path / "a.pdf", tmp_path / "a.docling.json", timeout=3,
                               heartbeat=lambda: beats.append(1))
        assert answer["ok"]
    finally:
        child.stop()
    assert beats


def test_stopping_a_child_that_never_started_is_harmless(tmp_path, monkeypatch):
    child = _child(tmp_path, monkeypatch, "ok")
    child.stop()
    child.stop()
    assert child.spawns == 0


def test_the_switch_is_on_by_default_and_can_be_turned_off(monkeypatch):
    monkeypatch.delenv("LITRAG_LAYOUT_CHILD", raising=False)
    assert enabled()
    for off in ("off", "0", "false", "no", "OFF"):
        monkeypatch.setenv("LITRAG_LAYOUT_CHILD", off)
        assert not enabled()
    monkeypatch.setenv("LITRAG_LAYOUT_CHILD", "on")
    assert enabled()


def test_a_child_that_hangs_before_it_is_ready_does_not_freeze_the_worker(tmp_path, monkeypatch):
    """The startup read used to have no deadline at all, so a child stuck importing torch — or
    fetching Docling's models on a first run — froze the worker with no heartbeat: the exact
    failure this module exists to remove, reintroduced by the module itself."""
    beats: list[int] = []
    child = _child(tmp_path, monkeypatch, "never-ready", counter=False)
    started = time.time()
    try:
        # bounded, retried once in a fresh child, then given up on with both attempts in the
        # reason — the same shape as a hang on a paper, because a slow start can also be transient
        with pytest.raises((LayoutCrashed, LayoutTimedOut)) as e:
            child.convert(tmp_path / "a.pdf", tmp_path / "a.docling.json", timeout=3,
                          heartbeat=lambda: beats.append(1))
        assert "starting up" in str(e.value)
    finally:
        child.stop()
    assert time.time() - started < 60  # bounded, and not by luck
    assert beats  # and the window was told the worker is alive while it waited


def test_a_child_that_dies_before_it_is_ready_is_reported_not_retried_forever(tmp_path, monkeypatch):
    child = _child(tmp_path, monkeypatch, "die-before-ready", counter=False)
    try:
        with pytest.raises(LayoutCrashed) as e:
            child.convert(tmp_path / "a.pdf", tmp_path / "a.docling.json", timeout=10)
        assert "before it was ready" in str(e.value)
        assert child.spawns == 2  # the one retry, and no more
    finally:
        child.stop()


def test_a_killed_child_is_waited_for_rather_than_left_behind(tmp_path, monkeypatch):
    """A multi-gigabyte torch child holding GPU memory must not be left for the collector."""
    child = _child(tmp_path, monkeypatch, "hang:1")
    try:
        child.convert(tmp_path / "a.pdf", tmp_path / "a.docling.json", timeout=2)
    finally:
        child.stop()
    assert child.proc is None or child.proc.returncode is not None


def test_the_document_is_written_whole_or_not_at_all(tmp_path, monkeypatch):
    """Everything downstream tests only that the raw document exists, so half a file is worse
    than none: `_has_a_tree` would call the paper read and `rebuild` would then choke on it."""
    child = _child(tmp_path, monkeypatch, "ok")
    out = tmp_path / "a.docling.json"
    try:
        child.convert(tmp_path / "a.pdf", out)
    finally:
        child.stop()
    assert json.loads(out.read_text(encoding="utf-8"))["texts"]
    assert not list(tmp_path.glob("*.part*"))  # nothing left half-written
