"""Charts read from figures into numbers: synthetic charts with known values (fixtures/charts,
made by make.py) read both ways — a PNG through OCR, a PDF through its own text layer — and held
to what they were drawn from; the scale, the words and the gate on their own."""

import json
import math
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from litrag_parser import charts
from litrag_parser.charts import Word, calibrate, number, power

CHARTS = Path(__file__).parent / "fixtures" / "charts"
TRUTH = json.loads((CHARTS / "truth.json").read_text())

VALUE_TOL = 0.015  # of the axis's range (in decades on a log axis)
ERROR_TOL = 0.025


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower().replace("µ", "u").replace("μ", "u").replace("-1", "-i"))


def _read(name, source):
    if source == "raster":
        return charts.read(np.array(Image.open(CHARTS / f"{name}.png").convert("RGB")))
    import pypdfium2 as pdfium

    from litrag_parser import figures

    pdf = pdfium.PdfDocument(str(CHARTS / f"{name}.pdf"))
    w, h = pdf[0].get_size()
    img, words, kind, _ = figures.crop(pdf, 1, (0, 0, w, h))
    assert kind == "vector" and words
    return charts.read(img, words, ocr=None)


def _span(plot):
    ticks = plot["y"]["ticks"]
    return (math.log10(max(ticks)) - math.log10(min(ticks))) if plot["y"]["scale"] == "log" else max(ticks) - min(ticks)


def _off(plot, got, want):
    if plot["y"]["scale"] == "log":
        return abs(math.log10(got) - math.log10(want)) / _span(plot)
    return abs(got - want) / _span(plot)


@pytest.mark.parametrize("source", ["vector", "raster"])
@pytest.mark.parametrize("name", list(TRUTH))
def test_a_chart_is_read_as_it_was_drawn(name, source):
    plots = _read(name, source)
    want = TRUTH[name]
    assert [p["status"] for p in plots] == ["read"] * len(want)
    for p, t in zip(plots, want):
        assert p["kind"] == t["kind"] and p["panel"] == t["panel"]
        assert p["y"]["scale"] == t["y_scale"] and p["y"]["residual"] <= charts.MAX_RESIDUAL
        assert _norm(t["y_label"]) in _norm(p["y"]["label"]) or source == "raster" and _norm(t["y_label"])[:8] in _norm(p["y"]["label"])
        if t["kind"] == "bar":
            assert [_norm(c) for c in p["categories"]] == [_norm(c) for c in t["categories"]]
        named = len(t["series"]) > 1
        for ts in t["series"]:
            s = next(s for s in p["series"] if not named or s["name"] == ts["name"])
            if t["kind"] == "bar":
                got = {_norm(v["category"]): v for v in s["values"]}
                pairs = [(got[_norm(c)], y, e) for c, y, e in zip(t["categories"], ts["values"], ts["errors"])]
            else:
                got = {round(v["x"]): v for v in s["values"]}
                pairs = [(got[x], y, e) for x, y, e in zip(ts["x"], ts["values"], ts["errors"]) if x in got]
                # two markers drawn over each other on the axis: one of them may not show, never more
                assert len(pairs) >= len(ts["values"]) - 1
            for v, y, e in pairs:
                assert _off(p, v["y"], y) <= VALUE_TOL, (ts["name"], v, y)
                if v["err_hi"] is not None and e:
                    assert _off(p, v["y"] + v["err_hi"], y + e) <= ERROR_TOL, (ts["name"], v, y, e)
            if t["kind"] == "bar":  # the error bars that show, show: at least those longer than a bar's cap
                visible = [v for v, y, e in pairs if e and e / _span(p) > 0.03 and p["y"]["scale"] == "linear"]
                assert all(v["err_hi"] is not None for v in visible)


def test_the_number_on_a_tick():
    assert [number(t) for t in ["40", "2.5", "−1", "-0.5", "0,5", "1,000", "50%", "(3)", "1 00"]] == [40, 2.5, -1, -0.5, 0.5, 1000, 50, 3, 100]
    assert [number(t) for t in ["A", "2a", "**", "10^3", ""]] == [None, None, None, None, None]
    assert [power(t) for t in ["10^3", "10³", "10{-2}", "10^ 4", "100", "3"]] == [3, 3, -2, 4, None, None]


def test_a_scale_holds_or_is_refused():
    lin = calibrate([(500, 0), (400, 10), (300, 20), (200, 30)], rising=-1)
    assert not lin.log and round(lin.value(350), 6) == 15 and lin.residual < 1e-9
    log = calibrate([(400, 10), (300, 100), (200, 1000), (100, 10000)], rising=-1)
    assert log.log and round(log.value(250), 3) == round(10**2.5, 3)
    one_wrong = calibrate([(500, 0), (400, 10), (300, 20), (200, 80), (100, 40)], rising=-1)  # "30" read as "80"
    assert one_wrong is not None and len(one_wrong.ticks) == 4 and 80 not in [v for _, v in one_wrong.ticks]
    assert calibrate([(500, 0), (400, 10)], rising=-1) is None  # two ticks are not enough
    assert calibrate([(500, 0), (400, 30), (300, 10), (200, 70), (100, 20)], rising=-1) is None  # nothing straight
    assert calibrate([(500, 30), (400, 20), (300, 10)], rising=-1) is None  # falling upwards: not a y axis


def test_grouped_bars_are_told_by_their_fills_repeating():
    k, g, w = (0, 0, 0), (120, 120, 120), (220, 220, 220)
    assert charts._period([k, g, w] * 6) == 3
    assert charts._period([k, g] * 4) == 2
    assert charts._period([k, g, w, (60, 60, 60)]) == 1  # four fills, no repeat: four bars of one series
    assert charts._period([k, k, k, k]) == 1


def test_a_photograph_or_a_frame_without_numbers_is_no_chart():
    rng = np.random.default_rng(0)
    photo = (rng.random((400, 600, 3)) * 255).astype(np.uint8)
    assert charts.read(photo, words=[]) == []
    framed = np.full((400, 600, 3), 255, np.uint8)
    framed[50:350, 100:103] = 0  # an L of two lines, with nothing beside it
    framed[347:350, 100:550] = 0
    framed[200:347, 200:260] = 90
    assert charts.read(framed, words=[]) == []
    # with numbers beside it, the same frame is a chart, and its bar is read on their scale
    words = [Word(60, 350 - 300 * v / 100 - 8, 90, 350 - 300 * v / 100 + 8, str(v)) for v in (0, 25, 50, 75, 100)]
    words.append(Word(205, 360, 255, 380, "Control"))
    (p,) = charts.read(framed, words=words)
    assert p["status"] == "read" and p["categories"] == ["Control"]
    (v,) = p["series"][0]["values"]
    assert abs(v["y"] - 100 * (350 - 200) / 300) <= 1.0


def test_a_scale_that_will_not_hold_is_said_and_no_value_given():
    framed = np.full((400, 600, 3), 255, np.uint8)
    framed[50:350, 100:103] = 0
    framed[347:350, 100:550] = 0
    framed[200:347, 200:260] = 90
    words = [Word(60, y - 8, 90, y + 8, t) for y, t in [(347, "0"), (272, "40"), (197, "15"), (122, "90")]]
    (p,) = charts.read(framed, words=words)
    assert p["status"] == "unread" and "no scale holds" in p["reason"] and p["series"] == []
