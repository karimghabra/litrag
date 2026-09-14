"""The boundary scorer: the judge's protocol, verdicts written as rows and replayed, the
worker building none unless it is switched on, and the calibration arithmetic."""

import sqlite3

from litrag_parser import boundary
from litrag_parser.boundary import BoundaryScorer, _best_threshold, _prf
from litrag_parser.judge import Judge, pair_key
from litrag_parser.store import open_store


class _Fake(BoundaryScorer):
    """A scorer whose likelihoods are whatever the test says."""

    def __init__(self, scores, threshold=1.0):
        super().__init__(model="fake", threshold=threshold)
        self._scores = scores
        self._model = object()  # loaded

    def score(self, a, b):
        self.scored += 1
        return self._scores.get((a[-20:], b[:20]))


def test_the_scorer_answers_the_judges_question_and_its_verdict_is_a_row(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    conn.execute("INSERT INTO papers(key, title, added_at) VALUES ('p', 't', '2026-01-01T00:00:00Z')")
    conn.commit()
    a, b = "The samples were washed and measured on the", "Instron 5944 at 1 mm/min until failure."
    c = "Methods were as described elsewhere."
    scorer = _Fake({(a[-20:], b[:20]): 2.4, (a[-20:], c[:20]): -0.3})
    judge = Judge(conn, "p", ask_model=False, scorer=scorer)
    assert judge(a, b) is True and judge(a, c) is False
    rows = conn.execute("SELECT pair, same, model FROM judgments ORDER BY pair").fetchall()
    assert {r["pair"] for r in rows} == {pair_key(a, b), pair_key(a, c)}
    assert all(r["model"] == "boundary:fake@1.0" for r in rows)
    assert judge.summary()["scored"] == 2 and judge.summary()["joined"] == 1
    # a rebuild: no scorer, no model; the rows answer
    again = Judge(conn, "p", ask_model=False)
    assert again(a, b) is True and again(a, c) is False and again.summary()["cached"] == 2
    assert again("something new", "Never seen before.") is None
    # a scorer with no verdict leaves the question to the generative judge, which is not asked here
    silent = _Fake({})
    assert Judge(conn, "p", ask_model=False, scorer=silent)("something new", "Never seen before.") is None
    assert silent.scored == 1


def test_no_model_means_no_scorer_and_the_judge_behaves_as_before(monkeypatch):
    s = BoundaryScorer(model="nowhere/does-not-exist")
    monkeypatch.setattr(s, "_load", lambda: False)
    assert s("a text that ends on the", "Next block.") is None and s.available() is False


def test_the_flag_and_the_default(monkeypatch):
    monkeypatch.delenv("LITRAG_BOUNDARY", raising=False)
    assert boundary.enabled() is boundary.DEFAULT_ON
    monkeypatch.setenv("LITRAG_BOUNDARY", "off")
    assert boundary.enabled() is False
    monkeypatch.setenv("LITRAG_BOUNDARY", "on")
    assert boundary.enabled() is True


def test_the_worker_builds_no_scorer_unless_switched_on(tmp_path, monkeypatch):
    from litrag_parser import worker as w

    monkeypatch.setattr(boundary, "BoundaryScorer", lambda *a, **k: (_ for _ in ()).throw(AssertionError("built a scorer")))
    monkeypatch.setenv("LITRAG_BOUNDARY", "off")
    assert w.Worker(tmp_path).scorer() is None
    monkeypatch.setenv("LITRAG_BOUNDARY", "0")
    assert w.Worker(tmp_path).scorer() is None
    monkeypatch.delenv("LITRAG_BOUNDARY")
    monkeypatch.setattr(boundary, "DEFAULT_ON", False)
    assert w.Worker(tmp_path).scorer() is None


def test_notes_are_rows_once_however_often_a_paper_is_rebuilt(tmp_path):
    from litrag_parser import worker as w
    from litrag_parser.tree import Node, Tree

    conn = open_store(tmp_path / "store.sqlite")
    conn.execute("INSERT INTO papers(key, title, added_at) VALUES ('p', 't', '2026-01-01T00:00:00Z')")
    conn.commit()
    root = Node("p", None, 0, 0, "document", "document", None, "other", None, [], "", None, None, "#/body")
    tree = Tree("t", [], root, {}, False, notes=[{"kind": "lane-suggested", "node_id": "p#section-1", "page": 2, "message": "reads as methods"}])
    w.Worker._note_events(conn, "p", tree)
    w.Worker._note_events(conn, "p", tree)
    assert conn.execute("SELECT COUNT(*) FROM events WHERE paper = 'p' AND stage = 'lane-suggested'").fetchone()[0] == 1


def test_calibration_arithmetic():
    rows = [(2.0, True), (1.5, True), (0.4, True), (1.2, False), (0.1, False), (-0.5, False)]
    assert _prf(rows, 1.0) == (2, 1, 1, 2)
    assert _best_threshold(rows) == 0.4  # at 1.5 two joins and none wrong (F1 0.8); at 0.4 all three and one wrong (F1 0.86)
    assert _best_threshold([(1.0, True), (0.0, False)]) == 1.0
