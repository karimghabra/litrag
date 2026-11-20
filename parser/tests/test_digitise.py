"""Plots digitised and sigmoids fitted: the calibration's arithmetic, a series found by its colour
in synthetic plots of known data (fixtures/digitise, made by make.py), the axes offered from a
figure's own numbers, the rows a person's work is kept as and the files it is exported to, the
search for plots, and the fits' numbers against the curves they were drawn from."""

import csv
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest

from litrag_parser import charts, digitise, digitise_bench, figures, fit
from litrag_parser.digitise import Axis, Calibration
from litrag_parser.store import file_paper, open_store

HERE = Path(__file__).parent / "fixtures" / "digitise"
TRUTH = json.loads((HERE / "truth.json").read_text())


# -- the calibration --------------------------------------------------------------------------------------------


def test_two_points_an_axis_map_pixels_to_values_and_back():
    cal = Calibration(Axis((100, 400), 0, (500, 400), 60), Axis((100, 400), 0, (100, 100), 0.8))
    assert cal.value(300, 250) == pytest.approx((30, 0.4))
    assert cal.pixel(45, 0.2) == pytest.approx((400, 325))
    assert Calibration.from_dict(cal.to_dict()).value(300, 250) == pytest.approx((30, 0.4))


def test_a_turned_plot_is_calibrated_as_well_as_a_square_one():
    a = math.radians(4)
    rot = lambda x, y: (200 + (x - 200) * math.cos(a) - (y - 300) * math.sin(a), 300 + (x - 200) * math.sin(a) + (y - 300) * math.cos(a))  # noqa: E731
    cal = Calibration(Axis(rot(100, 400), 0, rot(500, 400), 10), Axis(rot(100, 400), 0, rot(100, 100), 3))
    assert cal.value(*rot(300, 250)) == pytest.approx((5, 1.5), abs=1e-9)


def test_log_axes_are_calibrated_in_decades():
    cal = Calibration(Axis((0, 100), 0.01, (400, 100), 100, log=True), Axis((0, 100), 1, (0, 0), 1e4, log=True))
    assert cal.value(200, 50) == pytest.approx((1.0, 100.0))
    assert cal.pixel(10, 1000) == pytest.approx((300, 25))
    with pytest.raises(ValueError, match="above zero"):
        Calibration(Axis((0, 100), 0, (400, 100), 100, log=True), Axis((0, 100), 1, (0, 0), 10))


def test_a_calibration_that_fixes_nothing_is_refused():
    with pytest.raises(ValueError, match="in line"):
        Calibration(Axis((0, 100), 0, (100, 100), 1), Axis((0, 100), 0, (50, 100), 1))
    with pytest.raises(ValueError, match="different values"):
        Calibration(Axis((0, 100), 1, (100, 100), 1), Axis((0, 100), 0, (0, 0), 1))


# -- a series, by its colour ----------------------------------------------------------------------------------------


def _fixture(name):
    t = TRUTH[name]
    return digitise.load_image(HERE / t["image"]), Calibration.from_dict(t["calibration"]), t


def _region(cal, img):
    h, w = img.shape[:2]
    xs, ys = [cal.x.p1[0], cal.x.p2[0]], [cal.y.p1[1], cal.y.p2[1]]
    return [min(xs) - 0.03 * w, min(ys) - 0.06 * h, max(xs) + 0.03 * w, max(ys) + 0.01 * h]


def test_markers_are_found_at_their_values_on_a_log_axis():
    img, cal, t = _fixture("log_y_markers")
    s = t["series"][0]
    got = digitise.extract(img, s["colour"], region=_region(cal, img), calibration=cal)
    assert got["mode"] == "markers"
    score = digitise_bench.score_markers([(p["x"], p["y"]) for p in got["points"]], list(zip(s["x"], s["y"])), cal)
    assert score["recall"] == 1.0 and score["extra"] == 0 and score["max"] <= 0.012


def test_a_black_line_beside_black_axes_and_words_is_followed_and_not_the_axes():
    img, cal, t = _fixture("turbidity_lines")
    for s in t["series"]:
        got = digitise.extract(img, s["colour"], region=_region(cal, img), calibration=cal)
        assert got["mode"] == "line", s["name"]
        score = digitise_bench.score_line([(p["x"], p["y"]) for p in got["points"]], list(zip(s["x"], s["y"])), cal)
        assert score["p95"] <= 0.006 and score["cover"] >= 0.9, (s["name"], score)


def test_a_line_crossed_by_gridlines_on_a_log_x_axis_is_followed():
    img, cal, t = _fixture("log_x_line")
    s = t["series"][0]
    got = digitise.extract(img, s["colour"], region=_region(cal, img), calibration=cal, mode="line")
    score = digitise_bench.score_line([(p["x"], p["y"]) for p in got["points"]], list(zip(s["x"], s["y"])), cal)
    assert score["p95"] <= 0.006 and score["cover"] >= 0.95


def test_a_legend_s_own_marker_is_left_out_when_the_words_are_known():
    img, cal, t = _fixture("turbidity_markers")
    s = t["series"][1]
    region = _region(cal, img)
    plain = digitise.extract(img, s["colour"], region=region, calibration=cal)
    # the legend's swatch: the one marker found at none of the series' times
    lone = [(p["px"], p["py"]) for p in plain["points"] if not any(abs(p["x"] - x) < 1 for x in s["x"])]
    assert len(lone) == 1, "the legend's marker is found when no words say what it is"
    lx, ly = lone[0]
    words = [charts.Word(lx + 8, ly - 5, lx + 120, ly + 5, "Collagen + decorin")]
    named = digitise.extract(img, s["colour"], region=region, calibration=cal, words=words)
    assert named["legend"] == 1 and len(named["points"]) == len(plain["points"]) - 1


def test_a_noisy_scan_s_markers_are_found():
    img, cal, t = _fixture("noisy_scan")
    s = t["series"][0]
    got = digitise.extract(img, s["colour"], region=_region(cal, img), calibration=cal)
    score = digitise_bench.score_markers([(p["x"], p["y"]) for p in got["points"]], list(zip(s["x"], s["y"])), cal)
    assert got["mode"] == "markers" and score["recall"] == 1.0 and score["max"] <= 0.012


def test_the_figure_s_own_colours_are_offered_commonest_first():
    img, cal, t = _fixture("turbidity_lines")
    got = [digitise.rgb_of(p["colour"]) for p in digitise.palette(img, _region(cal, img))]
    for s in t["series"]:
        assert min(math.dist(c, digitise.rgb_of(s["colour"])) for c in got) < 45, s["name"]


# -- the axes, offered --------------------------------------------------------------------------------------------


def _vector(name):
    pdf = pdfium.PdfDocument(str(HERE / TRUTH[name]["pdf"]))
    w, h = pdf[0].get_size()
    img, words, kind, scale = figures.crop(pdf, 1, (0, 0, w, h))
    pdf.close()
    f = scale * 72 / 150
    c = TRUTH[name]["calibration"]
    truth = Calibration.from_dict({a: {**c[a], "p1": [v * f for v in c[a]["p1"]], "p2": [v * f for v in c[a]["p2"]]} for a in ("x", "y")})
    return img, words, truth


@pytest.mark.parametrize("name", ["turbidity_markers", "log_y_markers", "log_x_line"])
def test_a_vector_figure_s_axes_are_offered_from_its_text_layer(name):
    img, words, truth = _vector(name)
    got = digitise.auto_axes(img, words)
    assert got["found"], got["reason"]
    h, w = img.shape[:2]
    assert digitise_bench.axes_error(Calibration.from_dict(got["calibration"]), truth, w, h, got["plot_region"]) <= 0.002
    assert got["x"]["log"] == truth.x.log and got["y"]["log"] == truth.y.log
    assert got["x"]["unit"] and got["y"]["label"]


def test_an_image_s_axes_are_offered_from_ocr_with_their_units():
    img, cal, t = _fixture("turbidity_markers")
    got = digitise.auto_axes(img, None)
    assert got["found"] and got["confident"] and got["words"] == "ocr"
    h, w = img.shape[:2]
    assert digitise_bench.axes_error(Calibration.from_dict(got["calibration"]), cal, w, h, got["plot_region"]) <= 0.004
    assert got["x"]["unit"] == "min" and "Absorbance" in (got["y"]["label"] or "")


def test_a_photograph_offers_no_axes():
    rng = np.random.default_rng(1)
    img = (rng.random((200, 300, 3)) * 255).astype(np.uint8)
    got = digitise.auto_axes(img, [])
    assert not got["found"] and got["calibration"] is None and got["reason"]


# -- the rows, and the export -------------------------------------------------------------------------------------


def _library(tmp_path, name="turbidity_markers", caption="Figure 1. Turbidity of collagen over time at 37 °C."):
    """A library folder with one PDF paper whose one picture is a fixture plot, whole, captioned."""
    lib = tmp_path / "lib"
    (lib / "papers").mkdir(parents=True)
    shutil.copy(HERE / TRUTH[name]["pdf"], lib / "papers" / "paper.pdf")
    conn = open_store(lib / "store.sqlite")
    key = file_paper(conn, title="Collagen kinetics", file="paper.pdf", sha256="d1", fmt="pdf", doi="10.1000/dig", pmid=None, pmcid=None, now="t").key
    pdf = pdfium.PdfDocument(str(lib / "papers" / "paper.pdf"))
    w, h = pdf[0].get_size()
    pdf.close()
    fig = f"{key}#section-1#picture-1"
    rows = [(f"{key}#section-1", key, None, 0, 1, "section", "section_header", "results", "3 Results", "[]", "", None, None, None, None, None),
            (fig, key, f"{key}#section-1", 1, 2, "picture", "picture", "results", None, '["3 Results"]', "", 1, 0.0, 0.0, w, h),
            (f"{fig}#caption-1", key, fig, 0, 3, "caption", "caption", "results", None, '["3 Results"]', caption, 1, 0.0, h - 10, w, h)]
    conn.executemany("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, heading, ancestry, text, page, bbox_l, bbox_t, bbox_r, bbox_b) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("UPDATE papers SET status = 'parsed' WHERE key = ?", (key,))
    conn.commit()
    return lib, conn, key, fig


def _digitised(lib, conn, fig, series_i=0):
    got = digitise.render(lib, conn, fig)
    assert got["image"] and got["auto"]["found"]
    s = TRUTH["turbidity_markers"]["series"][series_i]
    pts = digitise.extract(got.pop("_img"), s["colour"], region=got["auto"]["plot_region"], calibration=Calibration.from_dict(got["auto"]["calibration"]))
    return got, s, pts


def test_a_figure_is_rendered_offered_found_saved_and_exported_traceably(tmp_path):
    lib, conn, key, fig = _library(tmp_path)
    got, s, pts = _digitised(lib, conn, fig)
    assert Path(got["image"]["path"]).is_file() and Path(got["image"]["path"]).with_suffix(".words.json").is_file()
    assert got["source"]["how"] == "pdf" and got["source"]["drawn"] == "vector" and got["source"]["label"] == "1"
    points = [{"px": p["px"], "py": p["py"], "how": "auto"} for p in pts["points"]][:-1] + [{"px": 300.0, "py": 400.0, "how": "added"}]
    saved = digitise.save(conn, fig, s["name"], got["auto"]["calibration"], points, colour=s["colour"],
                          method={"mode": pts["mode"], "tolerance": pts["tolerance"]}, image=got["image"], axes={"x": got["auto"]["x"], "y": got["auto"]["y"]})
    assert saved["series"] == s["name"] and len(saved["points"]) == len(points) and saved["axes"]["x"]["unit"] == "min"
    assert [p["x"] for p in saved["points"]] == sorted(p["x"] for p in saved["points"])
    assert any(p["how"] == "added" for p in saved["points"])
    # saved again under the same name: replaced, not added
    again = digitise.save(conn, fig, s["name"], got["auto"]["calibration"], points[:5], colour=s["colour"], image=got["image"])
    assert again["id"] == saved["id"] and len(digitise.of_figure(conn, fig)) == 1 and len(again["points"]) == 5
    saved = digitise.save(conn, fig, s["name"], got["auto"]["calibration"], points, colour=s["colour"], image=got["image"], axes={"x": got["auto"]["x"], "y": got["auto"]["y"]})
    f = fit.fit([(p["x"], p["y"]) for p in saved["points"] if p["how"] == "auto"], "logistic")
    digitise.save_fit(conn, saved["id"], f)
    out = digitise.export(conn, [saved["id"]], tmp_path / "out" / "turbidity", library="lib")
    rows = list(csv.reader(open(out["csv"])))
    assert rows[0] == ["Time (min)", "Absorbance (313 nm)"] or rows[0][0] == "Time (min)"
    assert len(rows) == len(points) + 1
    side = json.loads(Path(out["sidecar"]).read_text())
    assert side["paper"]["doi"] == "10.1000/dig" and side["figure"]["node"] == fig and side["figure"]["page"] == 1
    assert side["series"][0]["calibration"]["x"]["v2"] > side["series"][0]["calibration"]["x"]["v1"]
    assert side["series"][0]["points"][0].keys() >= {"x", "y", "px", "py", "how"} and side["image"]["sha256"] == got["image"]["sha256"]
    assert side["series"][0]["fits"]["logistic"]["derived"]["t_half"]["value"] == pytest.approx(f["derived"]["t_half"]["value"])
    assert out["fits"] and Path(out["fits"][0]).read_text().splitlines()[0] == "model,logistic"


def test_a_saved_series_follows_its_figure_through_a_rebuild_and_is_kept_when_none_is_like_it(tmp_path):
    lib, conn, key, fig = _library(tmp_path)
    got, s, pts = _digitised(lib, conn, fig)
    saved = digitise.save(conn, fig, "a", got["auto"]["calibration"], pts["points"], image=got["image"])
    # renumbered by another reading: found again by its caption's number
    conn.execute("UPDATE nodes SET parent = ? WHERE node_id = ?", (f"{key}#section-1#picture-7", f"{fig}#caption-1"))
    conn.execute("UPDATE nodes SET node_id = ? WHERE node_id = ?", (f"{key}#section-1#picture-7", fig))
    conn.commit()
    assert digitise.remap(conn, key) == {"moved": 1, "orphaned": 0}
    assert digitise.one(conn, saved["id"])["figure"] == f"{key}#section-1#picture-7"
    # a reading with no such figure: kept, marked an orphan
    conn.execute("DELETE FROM nodes WHERE paper = ? AND type IN ('picture', 'caption')", (key,))
    conn.commit()
    assert digitise.remap(conn, key) == {"moved": 0, "orphaned": 1}
    kept = digitise.one(conn, saved["id"])
    assert kept["orphan"] and len(kept["points"]) == len(pts["points"])


def test_an_xml_paper_without_a_pdf_says_why_it_cannot_be_drawn(tmp_path):
    conn = open_store(tmp_path / "store.sqlite")
    key = file_paper(conn, title="x", file="paper.xml", sha256="x1", fmt="jats", doi="10.1/x", pmid=None, pmcid=None, now="t").key
    conn.execute("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, ancestry, text) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (f"{key}#picture-1", key, None, 0, 1, "picture", "picture", "results", "[]", ""))
    conn.commit()
    got = digitise.render(tmp_path, conn, f"{key}#picture-1")
    assert got["image"] is None and "PMC" in got["source"]["reason"]
    with pytest.raises(ValueError, match="no figure"):
        digitise.render(tmp_path, conn, "nope")


# -- finding plots ----------------------------------------------------------------------------------------------


def test_plots_are_found_by_their_captions_trend_plots_first(tmp_path):
    lib, conn, key, fig = _library(tmp_path)
    other = f"{key}#section-1#picture-2"
    conn.executemany("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, ancestry, text, page, bbox_l, bbox_t, bbox_r, bbox_b) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     [(other, key, f"{key}#section-1", 2, 2, "picture", "picture", "results", "[]", "", 1, 0, 0, 100, 100),
                      (f"{other}#caption-1", key, other, 0, 3, "caption", "caption", "results", "[]", "Figure 2. SEM images of collagen fibrils after turbidity assay.", 1, 0, 0, 1, 1)])
    conn.commit()
    figures.read_paper(conn, key, lib / "papers" / "paper.pdf", ocr=lambda img: [])
    got = digitise.find(conn, "turbidity")
    assert [f["figure"] for f in got["figures"]] == [fig, other]
    assert got["figures"][0]["plot"] == "plot" and got["figures"][0]["trend"] and got["figures"][0]["image"]
    assert got["figures"][1]["plot"] in ("no plot", "unknown") and not got["figures"][1]["trend"]
    assert [f["figure"] for f in digitise.find(conn, "turbidity", trend=True)["figures"]] == [fig]
    assert digitise.find(conn, "nothing-like-this")["figures"] == []
    assert {f["figure"] for f in digitise.find(conn, "")["figures"]} == {fig, other}


def test_the_worker_answers_the_digitiser_s_ops(tmp_path):
    from litrag_parser.library import Library

    lib_dir, conn, key, fig = _library(tmp_path)
    conn.close()
    lib = Library(tmp_path, "lib", {})
    get = lambda: lib  # noqa: E731
    opened = digitise.answer("digitise_open", {"lib": "lib", "figure": fig}, get)
    assert opened["event"] == "digitise" and opened["image"]["path"] and opened["auto"]["found"] and opened["saved"] == [] and "_img" not in opened
    json.dumps(opened)  # every answer is JSON
    s = TRUTH["turbidity_markers"]["series"][0]
    ex = digitise.answer("digitise_extract", {"image": opened["image"]["path"], "colour": s["colour"], "region": opened["auto"]["plot_region"], "calibration": opened["auto"]["calibration"]}, get)
    assert ex["event"] == "extracted" and ex["mode"] == "markers" and len(ex["points"]) >= 10
    sv = digitise.answer("digitise_save", {"lib": "lib", "figure": fig, "series": "A", "calibration": opened["auto"]["calibration"], "points": ex["points"], "colour": s["colour"]}, get)
    assert sv["event"] == "digitised" and sv["series"]["series"] == "A"
    ft = digitise.answer("fit", {"lib": "lib", "id": sv["series"]["id"]}, get)
    assert ft["event"] == "fit" and set(ft["fits"]) == {"logistic", "gompertz"} and ft["preferred"] in ("logistic", "gompertz")
    assert set(digitise.answer("digitise_open", {"lib": "lib", "figure": fig}, get)["saved"][0]["fits"]) == {"logistic", "gompertz"}
    ex2 = digitise.answer("digitise_export", {"lib": "lib", "ids": [sv["series"]["id"]], "path": str(tmp_path / "a.csv")}, get)
    assert ex2["event"] == "exported" and Path(ex2["sidecar"]).is_file() and len(ex2["fits"]) == 2
    found = digitise.answer("plots", {"lib": "lib", "query": "turbidity"}, get)
    assert found["event"] == "plots" and found["figures"][0]["digitised"] == 1
    assert digitise.answer("digitise_delete", {"lib": "lib", "id": sv["series"]["id"]}, get)["deleted"] is True


# -- the fits -----------------------------------------------------------------------------------------------------


def _curve(model, t, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    if model == "logistic":
        y = 0.05 + 0.8 / (1 + np.exp(-0.25 * (t - 25)))
    else:
        a, mu, lam = 0.8, 0.05, 12.0
        y = 0.05 + a * np.exp(-np.exp(mu * math.e / a * (lam - t) + 1))
    return y + rng.normal(0, noise, len(t)) if noise else y


def test_a_logistic_fit_recovers_plateau_rate_midpoint_and_lag_within_its_intervals():
    t = np.linspace(0, 60, 40)
    want = {"plateau": 0.85, "max_rate": 0.8 * 0.25 / 4, "t_half": 25.0, "lag": 25 - 2 / 0.25, "baseline": 0.05}
    f = fit.fit(list(zip(t, _curve("logistic", t, 0.01))), "logistic")
    assert f["status"] == "fit" and f["r2"] > 0.99 and len(f["residuals"]) == 40
    for name, v in want.items():
        assert abs(f["derived"][name]["value"] - v) <= 4 * f["derived"][name]["se"], name
    # the 95% intervals hold the truth about 95% of the time, over fresh noise
    held = dict.fromkeys(want, 0)
    for seed in range(60):
        d = fit.fit(list(zip(t, _curve("logistic", t, 0.01, seed=seed))), "logistic")["derived"]
        for name, v in want.items():
            held[name] += d[name]["lo"] <= v <= d[name]["hi"]
    assert all(n / 60 >= 0.85 for n in held.values()), held


def test_a_gompertz_fit_recovers_its_rate_and_lag():
    t = np.linspace(0, 60, 40)
    f = fit.fit(list(zip(t, _curve("gompertz", t))), "gompertz")
    assert f["derived"]["max_rate"]["value"] == pytest.approx(0.05, rel=1e-3)
    assert f["derived"]["lag"]["value"] == pytest.approx(12.0, abs=1e-2)
    assert f["derived"]["plateau"]["value"] == pytest.approx(0.85, abs=1e-3)
    # its halfway time is where the curve is halfway
    th = f["derived"]["t_half"]["value"]
    assert float(_curve("gompertz", np.array([th]))[0]) == pytest.approx(0.05 + 0.4, abs=1e-3)


def test_the_better_model_is_preferred_and_too_few_points_are_refused():
    t = np.linspace(0, 60, 40)
    assert fit.both(list(zip(t, _curve("gompertz", t, 0.005))))["preferred"] == "gompertz"
    assert fit.both(list(zip(t, _curve("logistic", t, 0.005))))["preferred"] == "logistic"
    few = fit.fit([(0, 0), (1, 1), (2, 2)], "logistic")
    assert few["status"] == "unfit" and "at least" in few["reason"]
    flat = fit.fit([(i, 1.0) for i in range(10)], "gompertz")
    assert flat["status"] == "unfit"
    with pytest.raises(ValueError):
        fit.fit([(0, 0)] * 6, "hill")


def test_a_falling_sigmoid_is_fit_too():
    t = np.linspace(0, 30, 30)
    y = 1.0 - 0.9 / (1 + np.exp(-0.5 * (t - 12)))
    f = fit.fit(list(zip(t, y)), "logistic")
    assert f["derived"]["t_half"]["value"] == pytest.approx(12, abs=0.05) and f["derived"]["plateau"]["value"] == pytest.approx(0.1, abs=0.01)


def test_student_s_t_quantiles_match_the_tables():
    assert fit.t_ppf(0.975, 1) == pytest.approx(12.706, abs=2e-3)
    assert fit.t_ppf(0.975, 10) == pytest.approx(2.228, abs=1e-3)
    assert fit.t_ppf(0.975, 1000) == pytest.approx(1.962, abs=1e-3)


def test_a_fit_as_csv_carries_each_quantity_with_its_interval_and_unit():
    t = np.linspace(0, 60, 30)
    f = fit.fit(list(zip(t, _curve("logistic", t, 0.01))), "logistic")
    text = fit.params_csv(f, "min", "OD")
    rows = list(csv.reader(text.splitlines()))
    by = {r[0]: r for r in rows if r}
    assert by["t_half"][4] == "min" and by["max_rate"][4] == "OD/min" and by["plateau"][4] == "OD"
    assert float(by["t_half"][2]) < float(by["t_half"][1]) < float(by["t_half"][3])
