"""Fault injection on the layout child: a crash costs one paper, once.

The real failure is an intermittent native access violation inside Docling, which cannot be
summoned on demand and needs the models. `fake_layout.py` speaks the same protocol and fails in
each of the ways that matter — an abrupt exit, a wedge, a refusal — so what is measured here is
the supervision, which is the part that was written.
"""

import os
import sys
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
        # the reason names the real access-violation exit code (0xC0000005 = 3221225477),
        # because "the child died" without the code is not a diagnosis
        assert len(retries) == 1 and "3221225477" in retries[0], retries
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
