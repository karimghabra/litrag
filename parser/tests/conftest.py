"""What every test starts from: no oracle configured, and no test writing verdicts into
the user's real library root. `fake_oracle` is an oracle over a toy embedder for the tests
that need one."""

import re
import zlib

import pytest

from litrag_parser import lanes, meaning


@pytest.fixture(autouse=True)
def _no_oracle_leaks(monkeypatch):
    lanes._active = None
    monkeypatch.setenv("LITRAG_LANES", "off")  # the subprocess worker in test_worker inherits this
    yield
    lanes._active = None


def bag_embed(texts):
    """A toy embedder: a bag of words hashed into 256 buckets, plus a few features of shape
    (digits, initials, punctuation), so texts sharing words or shape lie near each other.
    Tests over it prove the plumbing — where the oracle is asked, where it is not, and that
    every answer replays from the store — not that the thresholds separate anything; the
    thresholds come from measurements on the corpora (NOTES.md)."""
    out = []
    for t in texts:
        t = t.split(": ", 1)[1] if ": " in t[:20] else t  # the task prefix stripped
        v = [0.0] * 264
        words = re.findall(r"[A-Za-z]+", t.lower())
        for w in words:
            v[zlib.crc32(w.encode()) % 256] += 1.0
        n = max(len(t), 1)
        v[256] = 4.0 * sum(ch.isdigit() for ch in t) / n
        v[257] = 4.0 * len(re.findall(r"\b[A-Z]\.", t)) / max(len(words), 1)
        v[258] = 4.0 * (t.count(",") + t.count(";")) / max(len(words), 1)
        v[259] = 2.0 * (t.count("(") + t.count(")")) / n
        v[260] = 1.0 if re.match(r"^\s*(?:\[\d+\]|\d+\.?)\s", t) else 0.0
        v[261] = 1.0 if re.search(r"\b(?:19|20)\d{2}\b", t) else 0.0
        v[262] = 3.0 * sum(1 for w in t.split() if w[:1].isupper()) / max(len(t.split()), 1)
        v[263] = 0.2  # so nothing is orthogonal to everything
        out.append(v)
    return out


@pytest.fixture
def fake_oracle(tmp_path, monkeypatch):
    """The process-wide oracle, over the toy embedder, with its verdicts in a temporary store."""
    monkeypatch.setattr(meaning.Oracle, "_embed", lambda self, texts: bag_embed(texts))
    monkeypatch.setenv("LITRAG_LANES", "on")
    o = lanes.configure(tmp_path / "lanes.sqlite")
    o.register(meaning.Kind("block", meaning.BLOCK_EXAMPLES, threshold=0.62, margin=0.03))  # the example paragraphs, in the toy embedder's space, not the shipped centroids
    return o
