"""The template fingerprint: what it must do before anything is allowed to lean on it.

Whether it actually recognises a publisher's layout is a measurement, not a property of the
code, and it lives in `campaign/reports/phase5.md`. What is asserted here is that the thing is
well behaved — a document compared with itself is at distance zero, a missing fingerprint is at
distance one rather than raising, two columns are told from one, and **no part of it is a
publisher's name**.
"""

import pytest

from litrag_parser.template import (
    Fingerprint, WEIGHTS, _block, _columns, distance, nearest,
)
from litrag_parser.typography import Row

TIMES = ("TimesNewRomanPSMT", 400, 9.5)
ARIAL = ("ArialMT", 400, 9.5)


def _row(l: float, r: float, b: float = 100.0, letters: int = 40, style=TIMES) -> Row:
    return Row(text="x" * letters, l=l, b=b, r=r, t=b + 10.0, spans=[(style, "x" * letters)])


def _fp(**kw) -> Fingerprint:
    base = dict(width=612.0, height=792.0, columns=2, body=TIMES,
                heads=(("bold", 10.0), ("larger", 14.0)), block=(0.1, 0.1, 0.9, 0.9), pages=8)
    return Fingerprint(**{**base, **kw})


# ---- the key ---------------------------------------------------------------------------------


def test_the_same_setting_is_the_same_key():
    assert _fp().key() == _fp().key()
    assert _fp().key() != _fp(columns=1).key()
    assert _fp().key() != _fp(body=ARIAL).key()


def test_nothing_in_a_fingerprint_names_a_publisher():
    """The whole point. A fingerprint is what a typesetter chose, not whose choice it was — so
    a rule built on one is document-relative by construction, which is what `PLAN.md` asks for
    and what ~150 literal publisher tokens in `tree.py` are not."""
    fields = set(Fingerprint.__dataclass_fields__)
    assert fields == {"width", "height", "columns", "body", "heads", "block", "pages"}


# ---- the distance ----------------------------------------------------------------------------


def test_a_paper_is_at_no_distance_from_itself():
    assert distance(_fp(), _fp()) == 0.0


def test_a_missing_fingerprint_is_far_rather_than_an_error():
    """A PDF with no text layer has no typography. That has to read as "nothing like anything",
    not as an exception in the middle of a corpus run."""
    assert distance(None, _fp()) == 1.0
    assert distance(_fp(), None) == 1.0
    assert distance(None, None) == 1.0


def test_the_distance_is_symmetric_and_bounded():
    a, b = _fp(), _fp(columns=1, body=("ArialMT", 400, 14.0), heads=(), block=(0.0, 0.0, 1.0, 1.0), width=595.0, height=842.0)
    assert distance(a, b) == distance(b, a)
    assert 0.0 <= distance(a, b) <= 1.0
    assert distance(a, b) == pytest.approx(sum(WEIGHTS.values()), abs=1e-9)  # nothing in common


def test_each_part_of_the_setting_moves_the_distance_by_its_weight():
    assert distance(_fp(), _fp(columns=1)) == pytest.approx(WEIGHTS["columns"])
    assert distance(_fp(), _fp(width=595.0, height=842.0)) == pytest.approx(WEIGHTS["page"])
    assert distance(_fp(), _fp(heads=())) == pytest.approx(WEIGHTS["heads"])


def test_the_same_face_at_another_size_is_nearer_than_another_face():
    """A template reset one point smaller for a supplement is the same template; a different
    face is a different one."""
    resized = distance(_fp(), _fp(body=("TimesNewRomanPSMT", 400, 12.0)))
    reface = distance(_fp(), _fp(body=("ArialMT", 400, 9.5)))
    assert 0 < resized < reface
    assert distance(_fp(), _fp(body=("TimesNewRomanPSMT", 400, 9.8))) == 0.0  # half a point is rounding


def test_nearest_over_an_empty_memory_is_a_first_contact():
    assert nearest(_fp(), {}) == (None, 1.0)
    assert nearest(None, {"a": _fp()}) == (None, 1.0)


def test_nearest_picks_the_nearer_of_two():
    known = {"near": _fp(heads=()), "far": _fp(columns=1, body=ARIAL, width=595.0, height=842.0)}
    name, how_far = nearest(_fp(), known)
    assert name == "near" and how_far == pytest.approx(WEIGHTS["heads"])


# ---- reading the page ------------------------------------------------------------------------


def test_two_left_edges_are_two_columns_and_one_is_one():
    two = [_row(60.0, 290.0) for _ in range(10)] + [_row(320.0, 550.0) for _ in range(10)]
    assert _columns(two, 612.0) == 2
    one = [_row(72.0, 540.0) for _ in range(20)]
    assert _columns(one, 612.0) == 1


def test_an_indent_is_not_a_second_column():
    """A one-column setting has a second left edge too — the first line of every paragraph. It
    sits a few points in, not across the gutter, which is why the test is where the edge is and
    not how many there are."""
    rows = [_row(72.0, 540.0) for _ in range(16)] + [_row(90.0, 540.0) for _ in range(6)]
    assert _columns(rows, 612.0) == 1


def test_one_stray_row_does_not_become_the_margin():
    rows = [_row(100.0, 500.0, b=200.0 + i) for i in range(40)]
    tight = _block(rows, 612.0, 792.0)
    rows.append(_row(5.0, 605.0, b=10.0))  # a folio across the foot of the page
    loose = _block(rows, 612.0, 792.0)
    assert loose == pytest.approx(tight, abs=0.01)
    assert loose[1] > 0.2, "the folio at the foot of the page is not where the type block starts"


def test_a_page_of_no_width_does_not_divide_by_zero():
    assert _columns([_row(10.0, 100.0)], 0.0) == 1
    assert _block([_row(10.0, 100.0)], 0.0, 0.0) == (0.0, 0.0, 1.0, 1.0)
