"""Headings named by meaning: the verdict is a deterministic function of the heading, the
prototypes and the model; it is kept in a store; with no embedder it is `other`."""

import math
import sqlite3

from litrag_parser import lanes, meaning
from litrag_parser.facets import role_of

WORDS = ["introduction", "background", "objectives", "methods", "materials", "experimental", "procedures", "design", "results", "findings", "discussion", "conclusions", "limitations", "outlook", "acknowledgements", "funding", "footnotes", "data", "collection", "analysis", "senescence", "ventricle", "immune"]


def fake_embed(texts):
    """A toy embedder: a fixed vocabulary of words; a text is the sum of its words' one-hot vectors."""
    out = []
    for t in texts:
        t = t.split(": ", 1)[1].lower() if ": " in t[:20] else t.lower()  # the task prefix stripped
        v = [1.0 if w in t else 0.0 for w in WORDS]
        if not any(v):
            v[-1] = 0.3  # a text of unknown words: a little of everything and nothing in particular
        out.append(v)
    return out


def test_a_heading_the_vocabulary_misses_is_named_when_it_lies_near_a_lane(tmp_path, monkeypatch):
    monkeypatch.setattr(meaning.Oracle, "_embed", lambda self, texts: fake_embed(texts))
    store = tmp_path / "lanes.sqlite"
    lanes.configure(store)
    assert role_of("3 | Results") == "results"  # the vocabulary, after the pipe is stripped
    assert role_of("5. Strengths and limitations") == "discussion"  # by meaning
    assert role_of("2. Data collection and analysis") == "methods"
    assert role_of("8 Immune cells in the aging ventricle") == "other"  # near nothing: unassignable beats misassigned
    assert lanes.active().summary()["kinds"]["heading"]["named"] >= 2
    # the verdicts are rows, and a second oracle over the same store answers without the embedder
    again = meaning.standard(store)
    monkeypatch.setattr(again, "_embed", lambda texts: (_ for _ in ()).throw(AssertionError("asked the embedder")))
    assert again.nearest("heading", "strengths and limitations").name == "discussion"
    assert again.nearest("heading", "immune cells in the aging ventricle").name == "other"


def test_with_no_embedder_an_unknown_heading_is_other(monkeypatch):
    assert role_of("5. Strengths and limitations") == "other"
    assert role_of("Materials and methods") == "methods"  # the vocabulary needs no embedder
    monkeypatch.setattr(meaning.Oracle, "_embed", lambda self, texts: None)  # Ollama down
    lanes.configure(None)
    assert role_of("5. Strengths and limitations") == "other"
    assert lanes.active().summary()["kinds"].get("heading", {}).get("asked", 0) == 0  # nothing answered, nothing counted


def test_the_margin_keeps_a_heading_between_two_lanes_unassigned(tmp_path, monkeypatch):
    monkeypatch.setattr(meaning.Oracle, "_embed", lambda self, texts: fake_embed(texts))
    lanes.configure(tmp_path / "lanes.sqlite")
    assert role_of("Results and limitations") == "other"  # as near results as discussion
    v = fake_embed(["Results"])[0]
    assert math.isclose(meaning._cos(v, v), 1.0, abs_tol=1e-6)


def test_the_rows_lanes_wrote_before_are_read_once_into_verdicts(tmp_path, monkeypatch):
    store = tmp_path / "lanes.sqlite"
    conn = sqlite3.connect(store)
    conn.execute("CREATE TABLE lanes (heading TEXT NOT NULL, model TEXT NOT NULL, lane TEXT NOT NULL, score REAL, margin REAL, at TEXT, PRIMARY KEY (heading, model))")
    conn.execute("INSERT INTO lanes VALUES ('strengths and limitations', 'nomic-embed-text', 'discussion', 0.9, 0.2, '2026-01-01T00:00:00Z')")
    conn.execute("INSERT INTO lanes VALUES ('immune cells in the aging ventricle', 'nomic-embed-text', 'other', 0.5, 0.01, '2026-01-01T00:00:00Z')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(meaning.Oracle, "_embed", lambda self, texts: (_ for _ in ()).throw(AssertionError("asked the embedder")))
    o = lanes.configure(store)
    assert o.nearest("heading", "strengths and limitations").name == "discussion"
    assert o.nearest("heading", "immune cells in the aging ventricle").name == "other"
    # the copy happens once: a row deleted from `verdicts` is not resurrected on the next open
    o._conn.execute("DELETE FROM verdicts WHERE key = 'strengths and limitations'")
    o._conn.commit()
    monkeypatch.setattr(meaning.Oracle, "_embed", lambda self, texts: None)
    assert lanes.configure(store).nearest("heading", "strengths and limitations").name == "other"
    names = {r[0] for r in sqlite3.connect(store).execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "lanes" not in names and "lanes_migrated" in names


def test_editing_a_kinds_threshold_asks_again_instead_of_replaying(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(meaning.Oracle, "_embed", lambda self, texts: (calls.append(len(texts)), fake_embed(texts))[1])
    store = tmp_path / "lanes.sqlite"
    for threshold in (0.5, 0.5, 0.9):
        o = meaning.Oracle(store)
        o.register(meaning.Kind("toy", {"methods": ["Methods"], "results": ["Results"]}, threshold, 0.05))
        o.nearest("toy", "experimental methods")
    assert len(calls) == 4  # examples and text once, replayed once, then examples and text again under the new threshold


def test_editing_a_kinds_examples_asks_again_instead_of_replaying(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(meaning.Oracle, "_embed", lambda self, texts: (calls.append(len(texts)), fake_embed(texts))[1])
    store = tmp_path / "lanes.sqlite"
    o = meaning.Oracle(store)
    o.register(meaning.Kind("toy", {"methods": ["Methods"], "results": ["Results"]}, 0.5, 0.05))
    assert o.nearest("toy", "experimental methods").name == "methods"
    o2 = meaning.Oracle(store)
    o2.register(meaning.Kind("toy", {"methods": ["Methods"], "results": ["Results"]}, 0.5, 0.05))
    n = len(calls)
    assert o2.nearest("toy", "experimental methods").name == "methods" and len(calls) == n  # replayed
    o3 = meaning.Oracle(store)
    o3.register(meaning.Kind("toy", {"methods": ["Methods", "Materials"], "results": ["Results"]}, 0.5, 0.05))
    o3.nearest("toy", "experimental methods")
    assert len(calls) > n  # new examples, new model string, asked again


def test_with_the_vocabulary_off_the_embedder_names_every_heading_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(meaning.Oracle, "_embed", lambda self, texts: fake_embed(texts))
    monkeypatch.setenv("LITRAG_VOCABULARY", "off")
    lanes.configure(tmp_path / "lanes.sqlite")
    assert role_of("Materials and methods") == "methods"  # by meaning now, not by the pattern
    assert role_of("Results and limitations") == "other"  # and nothing else answers
    assert role_of("Methods and Dataset") == "other"  # the word-count fallback is part of the vocabulary and is off too
    assert role_of("Materials and methods", meaning=False) == "other"  # depth has no vocabulary to lean on
    monkeypatch.setenv("LITRAG_VOCABULARY", "on")
    assert role_of("Results and limitations", meaning=False) == "other" and role_of("Materials and methods", meaning=False) == "methods"


def test_which_names_the_candidate_a_text_belongs_with(tmp_path, monkeypatch):
    monkeypatch.setattr(meaning.Oracle, "_embed", lambda self, texts: fake_embed(texts))
    o = meaning.standard(tmp_path / "lanes.sqlite")
    v = o.which("the data collection and analysis", ["introduction and background", "methods of data collection", "conclusions"])
    assert v.name == "1"
    assert o.which("senescence", ["introduction", "results"]).name == "other"  # near neither
    assert o.which("anything", []).name == "other"
