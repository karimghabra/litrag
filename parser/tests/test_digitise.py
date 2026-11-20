"""Plots digitised and sigmoids fitted: the calibration's arithmetic, a series found by its colour
in synthetic plots of known data (fixtures/digitise, made by make.py), the axes offered from a
figure's own numbers, the rows a person's work is kept as and the files it is exported to, the
search for plots, and the fits' numbers against the curves they were drawn from."""

import csv
import hashlib
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
    assert cal.value(400, 325) == pytest.approx((45, 0.2))
    assert Calibration.from_dict(cal.to_dict()).value(300, 250) == pytest.approx((30, 0.4))


def test_a_turned_plot_is_calibrated_as_well_as_a_square_one():
    a = math.radians(4)
    rot = lambda x, y: (200 + (x - 200) * math.cos(a) - (y - 300) * math.sin(a), 300 + (x - 200) * math.sin(a) + (y - 300) * math.cos(a))  # noqa: E731
    cal = Calibration(Axis(rot(100, 400), 0, rot(500, 400), 10), Axis(rot(100, 400), 0, rot(100, 100), 3))
    assert cal.value(*rot(300, 250)) == pytest.approx((5, 1.5), abs=1e-9)


def test_log_axes_are_calibrated_in_decades():
    cal = Calibration(Axis((0, 100), 0.01, (400, 100), 100, log=True), Axis((0, 100), 1, (0, 0), 1e4, log=True))
    assert cal.value(200, 50) == pytest.approx((1.0, 100.0))
    assert cal.value(300, 25) == pytest.approx((10.0, 1000.0))
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
    (lib / "papers").mkdir(parents=True, exist_ok=True)
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
    saved = digitise.save(lib, conn, fig, s["name"], got["auto"]["calibration"], points, colour=s["colour"],
                          method={"mode": pts["mode"], "tolerance": pts["tolerance"]}, image=got["image"], axes={"x": got["auto"]["x"], "y": got["auto"]["y"]})
    assert saved["series"] == s["name"] and len(saved["points"]) == len(points) and saved["axes"]["x"]["unit"] == "min"
    assert [p["x"] for p in saved["points"]] == sorted(p["x"] for p in saved["points"])
    assert any(p["how"] == "added" for p in saved["points"])
    # saved again under the same name: replaced, not added
    again = digitise.save(lib, conn, fig, s["name"], got["auto"]["calibration"], points[:5], colour=s["colour"], image=got["image"], replace=True)
    assert again["id"] == saved["id"] and len(digitise.of_figure(conn, fig)) == 1 and len(again["points"]) == 5
    saved = digitise.save(lib, conn, fig, s["name"], got["auto"]["calibration"], points, colour=s["colour"], image=got["image"], replace=True, axes={"x": got["auto"]["x"], "y": got["auto"]["y"]})
    f = fit.fit([(p["x"], p["y"]) for p in saved["points"] if p["how"] == "auto"], "logistic")
    digitise.save_fit(conn, saved["id"], f)
    out = digitise.export(conn, [saved["id"]], tmp_path / "out" / "turbidity", library="lib")
    assert len(out["files"]) == 1 and out["points"] == len(points)
    out = out["files"][0]
    rows = list(csv.reader(open(out["csv"])))
    assert rows[0] == [TRUTH["turbidity_markers"]["x_label"], TRUTH["turbidity_markers"]["y_label"]] == ["Time (min)", "Absorbance (313 nm)"]
    assert len(rows) == len(points) + 1
    side = json.loads(Path(out["sidecar"]).read_text())
    assert side["paper"]["doi"] == "10.1000/dig" and side["figure"]["node"] == fig and side["series"][0]["page"] == 1
    assert side["series"][0]["calibration"]["x"]["v2"] > side["series"][0]["calibration"]["x"]["v1"]
    assert side["series"][0]["points"][0].keys() >= {"x", "y", "px", "py", "how"} and side["series"][0]["image"]["sha256"] == got["image"]["sha256"]
    assert side["series"][0]["fits"]["logistic"]["derived"]["t_half"]["value"] == pytest.approx(f["derived"]["t_half"]["value"])
    assert out["fits"] and Path(out["fits"][0]).read_text().splitlines()[0] == "model,logistic"


def test_a_second_curve_saved_under_the_first_s_name_does_not_take_its_place_unasked(tmp_path):
    """The turbidity figure's two curves, digitised one after the other. Saving the second under
    the name the first was saved with is refused, and the first stays as it was; under a name of
    its own both are kept; only a save that says it replaces the first does."""
    lib, conn, key, fig = _library(tmp_path)
    got, first, a_pts = _digitised(lib, conn, fig, 0)
    _, second, b_pts = _digitised(lib, conn, fig, 1)
    cal = got["auto"]["calibration"]
    assert first["colour"] != second["colour"] and a_pts["points"] != b_pts["points"]
    a = digitise.save(lib, conn, fig, "series 1", cal, a_pts["points"], colour=first["colour"], image=got["image"])
    digitise.save_fit(conn, a["id"], fit.fit([(p["x"], p["y"]) for p in a["points"]], "logistic"))
    with pytest.raises(ValueError, match="already"):
        digitise.save(lib, conn, fig, "series 1", cal, b_pts["points"], colour=second["colour"], image=got["image"])
    kept = digitise.one(conn, a["id"])
    assert kept["points"] == a["points"] and kept["colour"] == first["colour"] and set(kept["fits"]) == {"logistic"}
    b = digitise.save(lib, conn, fig, "series 2", cal, b_pts["points"], colour=second["colour"], image=got["image"])
    assert [(s["series"], s["colour"], len(s["points"])) for s in digitise.of_figure(conn, fig)] == [
        ("series 1", first["colour"], len(a_pts["points"])), ("series 2", second["colour"], len(b_pts["points"]))]
    over = digitise.save(lib, conn, fig, "series 1", cal, b_pts["points"], colour=second["colour"], image=got["image"], replace=True)
    assert over["id"] == a["id"] and over["points"] == b["points"] and over["fits"] == {} and len(digitise.of_figure(conn, fig)) == 2


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


CAL = {"x": {"p1": [100, 400], "v1": 0, "p2": [500, 400], "v2": 60, "log": False}, "y": {"p1": [100, 400], "v1": 0, "p2": [100, 100], "v2": 0.8, "log": False}}
PTS = [{"px": 100 + 40 * i, "py": 400 - 30 * i, "how": "added"} for i in range(8)]


def _picture(conn, key, n, caption):
    """A picture node of a paper with its caption, placed nowhere; its id."""
    fig = f"{key}#picture-{n}"
    conn.executemany("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, ancestry, text) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     [(fig, key, None, n, 1, "picture", "picture", "results", "[]", ""), (f"{fig}#caption-1", key, fig, 0, 2, "caption", "caption", "results", "[]", caption)])
    conn.commit()
    return fig


def _panel(lib, conn, key, n, box, caption, file="paper.pdf"):
    """A picture of a PDF paper drawn from the part `box` — fractions of the page, from its top
    left — of the first page of its file; its id."""
    pdf = pdfium.PdfDocument(str(lib / "papers" / file))
    w, h = pdf[0].get_size()
    pdf.close()
    fig = f"{key}#panel-{n}"
    conn.executemany("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, ancestry, text, page, bbox_l, bbox_t, bbox_r, bbox_b) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     [(fig, key, None, 20 + n, 1, "picture", "picture", "results", "[]", "", 1, box[0] * w, box[1] * h, box[2] * w, box[3] * h),
                      (f"{fig}#caption-1", key, fig, 0, 2, "caption", "caption", "results", "[]", caption, 1, 0.0, h - 10, w, h)])
    conn.commit()
    return fig


LEFT, RIGHT, WHOLE = (0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, 1.0)


def _saved(lib, conn, fig, name, pts=PTS, cal=CAL, **kw):
    """A series saved on a figure opened as it is drawn now."""
    return digitise.save(lib, conn, fig, name, cal, pts, image=digitise.render(lib, conn, fig, offer=False)["image"], **kw)


def _shown(lib, fig, data=b"a picture"):
    """A figure's picture put where opening it writes it, for a figure no PDF here draws; what `save` is given of it."""
    path = digitise.picture_path(lib, fig)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"width": 600, "height": 450, "scale": 2.0, "sha256": hashlib.sha256(data).hexdigest(), "path": str(path)}


def _rename(conn, old, new):
    conn.execute("UPDATE nodes SET parent = ? WHERE parent = ?", (new, old))
    conn.execute("UPDATE nodes SET node_id = ? WHERE node_id = ?", (new, old))


def test_a_series_stays_with_the_figure_drawn_as_its_picture_whatever_its_id_or_number(tmp_path):
    """Two pictures whose captions both read "Figure 2", each with a series under the window's
    default name. A reading that trades their node ids leaves each series with the picture it was
    read from: no caption's number sends one to the other's."""
    lib, conn, key, _ = _library(tmp_path)
    left, right = _panel(lib, conn, key, 1, LEFT, "Figure 2. Turbidity over time."), _panel(lib, conn, key, 2, RIGHT, "Figure 2. Continued.")
    a = _saved(lib, conn, left, "series 1")
    b = _saved(lib, conn, right, "series 1", PTS[:5])
    assert a["image"]["sha256"] != b["image"]["sha256"]
    assert digitise.follow(lib, conn, key) == []
    assert [s["id"] for s in digitise.of_figure(conn, left)] == [a["id"]] and [s["id"] for s in digitise.of_figure(conn, right)] == [b["id"]]
    _rename(conn, left, "tmp")  # the left picture is `right` now, and the right one `left`
    _rename(conn, right, left)
    _rename(conn, "tmp", right)
    conn.commit()
    assert digitise.follow(lib, conn, key) == []
    assert [(s["id"], len(s["points"])) for s in digitise.of_figure(conn, right)] == [(a["id"], 8)]
    assert [(s["id"], len(s["points"])) for s in digitise.of_figure(conn, left)] == [(b["id"], 5)]
    assert digitise.picture_sha(lib, conn, right) == a["image"]["sha256"]
    assert digitise.follow(lib, conn, key) == [] and digitise.orphans(conn, key) == []  # twice changes nothing


def test_a_reading_that_cuts_the_figure_differently_leaves_its_series_for_review_until_it_is_cut_so_again(tmp_path):
    lib, conn, key, _ = _library(tmp_path)
    fig = _panel(lib, conn, key, 1, LEFT, "Figure 2. Turbidity over time.")
    a = _saved(lib, conn, fig, "lag")
    was = conn.execute("SELECT bbox_r FROM nodes WHERE node_id = ?", (fig,)).fetchone()[0]
    conn.execute("UPDATE nodes SET bbox_r = ? WHERE node_id = ?", (was * 0.8, fig))
    conn.commit()
    said = "1 digitised series is of a picture this reading no longer draws: kept, for review in the Plots tab"
    assert digitise.follow(lib, conn, key) == [("digitise", said)]
    assert [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'digitise'", (key,))] == [said]
    assert digitise.of_figure(conn, fig) == [] and [(s["id"], len(s["points"])) for s in digitise.orphans(conn, key)] == [(a["id"], 8)]
    assert digitise.follow(lib, conn, key) == []  # said once, not at every reading
    conn.execute("UPDATE nodes SET bbox_r = ? WHERE node_id = ?", (was, fig))
    conn.commit()
    assert digitise.follow(lib, conn, key) == []
    assert [s["id"] for s in digitise.of_figure(conn, fig)] == [a["id"]] and digitise.orphans(conn, key) == []


def test_a_series_for_review_is_no_figure_s_until_a_person_puts_it_on_one(tmp_path):
    """Figure 1 goes from a reading and figure 2 is given its node id. The series read from
    figure 1 is listed for review with the picture it was read from; it is not among figure 2's,
    is not counted as figure 2's, and a series saved on figure 2 under its name is a new one.
    Put on a figure by a person, it is that figure's through later readings, its values as saved."""
    from litrag_parser.library import Library

    lib_dir, conn, key, _ = _library(tmp_path)
    lib = Library(tmp_path, "lib", {})
    get = lambda: lib  # noqa: E731
    one, two = _panel(lib_dir, conn, key, 1, LEFT, "Figure 1. Turbidity over time."), _panel(lib_dir, conn, key, 2, RIGHT, "Figure 2. Absorbance over time.")
    a = _saved(lib_dir, conn, one, "lag")
    b = _saved(lib_dir, conn, two, "other", PTS[:5])
    conn.execute("DELETE FROM nodes WHERE node_id LIKE ?", (one + "%",))
    _rename(conn, two, one)
    conn.commit()
    assert len(digitise.follow(lib_dir, conn, key)) == 1
    assert [s["id"] for s in digitise.of_figure(conn, one)] == [b["id"]]
    opened = digitise.answer("digitise_open", {"lib": "lib", "figure": one}, get)
    assert [s["id"] for s in opened["saved"]] == [b["id"]]
    assert [(s["id"], s["figure_label"], s["orphan"]) for s in opened["orphans"]] == [(a["id"], "1", True)]
    kept = Path(opened["orphans"][0]["picture"])
    assert hashlib.sha256(kept.read_bytes()).hexdigest() == a["image"]["sha256"] != opened["image"]["sha256"]  # the picture it was read from, kept
    c = digitise.answer("digitise_save", {"lib": "lib", "figure": one, "series": "lag", "calibration": CAL, "points": PTS[:3], "image": opened["image"]}, get)["series"]
    assert c["id"] not in (a["id"], b["id"]) and c["figure_label"] == "2" and len(c["points"]) == 3
    assert set(c["image"]) == {"width", "height", "scale", "sha256"}  # the image's size and hash, not where it lay on this machine
    lost = digitise.one(conn, a["id"])
    assert lost["orphan"] and len(lost["points"]) == 8 and lost["figure_label"] == "1"
    assert [s["id"] for s in digitise.of_figure(conn, one)] == [b["id"], c["id"]]
    assert {f["figure"]: f["digitised"] for f in digitise.find(conn, "")["figures"]}[one] == 2
    assert digitise.follow(lib_dir, conn, key) == [] and digitise.one(conn, a["id"])["orphan"]  # no reading puts it on a figure
    with pytest.raises(ValueError, match="already"):  # nor may a person, over one of its name
        digitise.answer("digitise_attach", {"lib": "lib", "series_id": a["id"], "figure": one}, get)
    assert digitise.answer("digitise_delete", {"lib": "lib", "series_id": c["id"]}, get)["deleted"]
    put = digitise.answer("digitise_attach", {"lib": "lib", "series_id": a["id"], "figure": one}, get)["series"]
    assert (put["figure"], put["orphan"], put["figure_label"]) == (one, False, "2")
    assert put["points"] == a["points"] and put["calibration"] == a["calibration"] and put["image"] == a["image"]
    assert digitise.answer("digitise_open", {"lib": "lib", "figure": one}, get)["orphans"] == []
    _rename(conn, one, f"{key}#panel-9")
    conn.commit()
    assert digitise.follow(lib_dir, conn, key) == []
    assert [s["id"] for s in digitise.of_figure(conn, f"{key}#panel-9")] == [a["id"], b["id"]]
    ex = digitise.answer("digitise_export", {"lib": "lib", "ids": [a["id"], b["id"]], "path": str(tmp_path / "out.csv")}, get)
    text = Path(ex["files"][0]["sidecar"]).read_text()
    assert str(tmp_path / "lib") not in text  # the sidecar travels: no path of this library in it
    # one figure's series, pixels of two pictures: each is traced to its own
    assert {s["name"]: s["image"]["sha256"] for s in json.loads(text)["series"]} == {"lag": a["image"]["sha256"], "other": b["image"]["sha256"]}
    assert {s["name"]: s["bbox_pt"] for s in json.loads(text)["series"]} == {"lag": a["bbox"], "other": b["bbox"]} and a["bbox"] != b["bbox"]


def test_a_series_is_saved_only_of_the_figure_s_picture_as_it_was_opened(tmp_path):
    lib, conn, key, fig = _library(tmp_path)
    with pytest.raises(ValueError, match="open the figure again"):  # never opened: no picture its pixels could be of
        digitise.save(lib, conn, fig, "a", CAL, PTS, image={"width": 1, "height": 1, "scale": 1.0, "sha256": "0" * 64})
    image = digitise.render(lib, conn, fig, offer=False)["image"]
    with pytest.raises(ValueError, match="open the figure again"):
        digitise.save(lib, conn, fig, "a", CAL, PTS, image={**image, "sha256": "0" * 64})
    assert digitise.of_figure(conn, fig) == []
    assert digitise.save(lib, conn, fig, "a", CAL, PTS, image=image)["image"]["sha256"] == image["sha256"]


def test_the_judge_s_own_command_keeps_digitised_series_as_a_rebuild_does(tmp_path, monkeypatch, capsys):
    """`python -m litrag_parser.judge` replaces a paper's nodes as a rebuild does: a series whose
    picture the new reading does not draw is kept, and said to be for review. The model is never asked."""
    from litrag_parser import judge
    from litrag_parser.library import create_library, safe_key

    from test_worker import FIXTURES

    monkeypatch.setattr(judge, "available", lambda *a, **kw: True)
    monkeypatch.setattr(judge, "ask_ollama", lambda *a, **kw: None)
    lib = create_library(tmp_path, "Micromachines")
    conn = open_store(lib.store_path)
    key = file_paper(conn, title="x", file="", sha256="aa", fmt="jats", doi="10.3390/mi15070851", pmid=None, pmcid=None, now="t").key
    shutil.copy(FIXTURES / "PMC11278924.xml", lib.papers_dir / "paper.xml")
    shutil.copy(FIXTURES / "PMC11278924.jats.docling.json", lib.parsed_dir / f"{safe_key(key)}.docling.json")
    conn.execute("UPDATE papers SET file = 'paper.xml', status = 'parsed' WHERE key = ?", (key,))
    fig = _picture(conn, key, 1, "Figure 1. An earlier reading's.")
    a = digitise.save(lib.dir, conn, fig, "a", CAL, PTS, image=_shown(lib.dir, fig))["id"]
    conn.close()
    judge.main(["--lib", str(lib.dir), "--key", key])
    conn = open_store(lib.store_path)
    assert digitise.one(conn, a)["orphan"] and len(digitise.one(conn, a)["points"]) == 8
    said = "1 digitised series is of a picture this reading no longer draws: kept, for review in the Plots tab"
    assert [r[0] for r in conn.execute("SELECT detail FROM events WHERE paper = ? AND stage = 'digitise'", (key,))] == [said]
    assert said in capsys.readouterr().out
    conn.close()


def test_series_of_different_axes_are_exported_apart_and_an_orphan_is_left_out(tmp_path):
    """Panels of one figure: time in minutes on one, concentration in mg/mL on the other. No
    value of the second is written under the first's header."""
    lib, conn, key, fig = _library(tmp_path)
    time = {"x": {"label": "Time", "unit": "min"}, "y": {"label": "Absorbance", "unit": None}}
    dose = {"x": {"label": "Concentration", "unit": "mg/mL"}, "y": {"label": "Lag time", "unit": "min"}}
    ids = [_saved(lib, conn, fig, name, pts, axes=axes)["id"] for name, pts, axes in (("a1", PTS, time), ("b1", PTS[:3], dose), ("a2", PTS[:5], time), ("gone", PTS[:2], time))]
    t = np.linspace(0, 60, 30)
    digitise.save_fit(conn, ids[1], fit.fit(list(zip(t, _curve("logistic", t))), "logistic"))
    conn.execute("UPDATE digitised SET orphan = 1 WHERE id = ?", (ids[3],))
    conn.commit()
    out = digitise.export(conn, ids, tmp_path / "fig.csv", library="lib")
    assert out["points"] == 8 + 3 + 5 and [Path(f["csv"]).name for f in out["files"]] == ["fig.1.csv", "fig.2.csv"]
    first, second = (list(csv.reader(open(f["csv"]))) for f in out["files"])
    assert first[0] == ["series", "Time (min)", "Absorbance"] and [r[0] for r in first[1:]] == ["a1"] * 8 + ["a2"] * 5
    assert second[0] == ["Concentration (mg/mL)", "Lag time (min)"] and len(second) == 4
    sides = [json.loads(Path(f["sidecar"]).read_text()) for f in out["files"]]
    assert [s["csv"] for s in sides] == ["fig.1.csv", "fig.2.csv"]
    assert sides[0]["axes"]["x"]["unit"] == "min" and [s["name"] for s in sides[0]["series"]] == ["a1", "a2"]
    assert sides[1]["axes"]["x"]["unit"] == "mg/mL" and [s["name"] for s in sides[1]["series"]] == ["b1"]
    assert out["files"][0]["fits"] == [] and {"logistic": Path(out["files"][1]["fits"][0]).name} == sides[1]["series"][0]["fit_csv"]
    by = {r[0]: r for r in csv.reader(open(out["files"][1]["fits"][0])) if r}
    assert by["t_half"][4] == "mg/mL"  # the fit's units are its own panel's
    with pytest.raises(ValueError, match="no series"):
        digitise.export(conn, [ids[3]], tmp_path / "gone.csv")
    # a log axis is another set of axes, though the labels agree
    log = _saved(lib, conn, fig, "a3", PTS, {**CAL, "y": {**CAL["y"], "v1": 0.01, "log": True}}, axes=time)["id"]
    assert len(digitise.export(conn, [ids[0], log], tmp_path / "log.csv")["files"]) == 2


def test_each_fit_of_each_series_has_a_file_of_its_own_that_the_sidecar_names(tmp_path):
    """Two models a series, and two names that are one once made safe for a file name: four fit
    files, none written over another, each named beside its series and model."""
    lib, conn, key, fig = _library(tmp_path)
    t = np.linspace(0, 60, 30)
    plateaus = {"a b": 0.85, "a_b": 1.7}
    for name, plateau in plateaus.items():
        did = _saved(lib, conn, fig, name)["id"]
        for model in fit.MODELS:
            digitise.save_fit(conn, did, fit.fit(list(zip(t, _curve("logistic", t) * plateau / 0.85)), model))
    out = digitise.export(conn, [s["id"] for s in digitise.of_figure(conn, fig)], tmp_path / "fig.csv")["files"][0]
    side = json.loads(Path(out["sidecar"]).read_text())
    named = {(s["name"], model): tmp_path / f for s in side["series"] for model, f in s["fit_csv"].items()}
    assert set(named) == {(name, model) for name in plateaus for model in fit.MODELS}
    assert len(set(named.values())) == 4 and sorted(str(f) for f in named.values()) == sorted(out["fits"])
    for (name, model), f in named.items():
        by = {r[0]: r for r in csv.reader(f.read_text().splitlines()) if r}
        assert by["model"][1] == model and float(by["plateau"][1]) == pytest.approx(plateaus[name], rel=0.02), (name, model)


def test_an_image_file_is_kept_under_the_library_s_digitised_folder_or_with_no_library_somewhere_readable(tmp_path):
    from litrag_parser.library import Library

    (tmp_path / "lib").mkdir()
    lib = Library(tmp_path, "lib", {})
    got = digitise.answer("digitise_open", {"lib": "lib", "path": str(HERE / "turbidity_markers.png")}, lambda: lib)
    assert Path(got["image"]["path"]).parent == lib.dir / "digitised" and Path(got["image"]["path"]).is_file()
    none = digitise.answer("digitise_open", {"path": str(HERE / "turbidity_markers.png")}, lambda: pytest.fail("no library was named"))
    assert none["lib"] is None and Path(none["image"]["path"]).is_file() and none["image"]["sha256"] == got["image"]["sha256"]
    s = TRUTH["turbidity_markers"]["series"][0]
    ex = digitise.answer("digitise_extract", {"image": none["image"]["path"], "colour": s["colour"]}, None)
    assert len(ex["points"]) >= 10


def test_a_series_of_an_xml_paper_s_figure_keeps_the_page_and_box_it_was_opened_at(tmp_path):
    """The store knows no place for an XML paper's figure: the open finds it on the PDF kept
    beside the XML, and the save is given it back."""
    from litrag_parser.library import Library

    (tmp_path / "lib").mkdir()
    lib = Library(tmp_path, "lib", {})
    conn = open_store(lib.store_path)
    key = file_paper(conn, title="x", file="paper.xml", sha256="x1", fmt="jats", doi="10.1/x", pmid=None, pmcid=None, now="t").key
    conn.execute("UPDATE papers SET figures_file = 'x.figures.pdf' WHERE key = ?", (key,))
    fig = _picture(conn, key, 1, "Figure 1. Turbidity over time.")
    source = {**digitise.figure_source(conn, fig), "page": 3, "bbox": [40.0, 100.5, 300.0, 280.0]}
    assert source["how"] == "beside"
    conn.close()
    sv = digitise.answer("digitise_save", {"lib": "lib", "figure": fig, "series": "A", "calibration": CAL, "points": PTS, "source": source, "image": _shown(lib.dir, fig)}, lambda: lib)["series"]
    assert (sv["page"], sv["bbox"], sv["figure_label"], sv["paper"]) == (3, [40.0, 100.5, 300.0, 280.0], "1", key)
    ex = digitise.answer("digitise_export", {"lib": "lib", "ids": [sv["id"]], "path": str(tmp_path / "a.csv")}, lambda: lib)
    side = json.loads(Path(ex["files"][0]["sidecar"]).read_text())
    assert side["series"][0]["page"] == 3 and side["series"][0]["bbox_pt"] == [40.0, 100.5, 300.0, 280.0]


def test_a_stub_s_digitised_series_go_to_the_paper_it_was_a_stub_of(tmp_path):
    """The same bytes filed first under their hash, then under their DOI: the stub's rows go, but
    not a person's series. It is the paper's, for review, and a reading of the paper that draws
    its picture puts it on the figure drawn so."""
    lib, conn, paper, own = _library(tmp_path)
    stub = file_paper(conn, title="its bytes", file="paper.pdf", sha256="pdf", fmt="pdf", doi=None, pmid=None, pmcid=None, now="t").key
    a = _saved(lib, conn, _panel(lib, conn, stub, 1, WHOLE, "Figure 1. Turbidity over time."), "a")["id"]
    filed = file_paper(conn, title="the paper", file="paper.pdf", sha256="pdf", fmt="pdf", doi="10.1000/dig", pmid=None, pmcid=None, now="t")
    conn.commit()
    assert filed.key == paper and [r[0] for r in conn.execute("SELECT key FROM papers")] == [paper]
    assert [(s["id"], s["paper"], len(s["points"])) for s in digitise.orphans(conn, paper)] == [(a, paper, 8)]
    assert digitise.of_figure(conn, own) == []
    assert digitise.follow(lib, conn, paper) == []
    assert [(s["id"], s["orphan"], len(s["points"])) for s in digitise.of_figure(conn, own)] == [(a, False, 8)]


def test_a_stub_dropped_for_a_pdf_kept_beside_an_xml_paper_hands_over_its_series(tmp_path):
    from litrag_parser.store import sha256_of

    lib = tmp_path / "lib"
    (lib / "papers").mkdir(parents=True)
    conn = open_store(lib / "store.sqlite")
    pdf = HERE / TRUTH["turbidity_markers"]["pdf"]
    paper = file_paper(conn, title="the paper", file="paper.xml", sha256="xml", fmt="jats", doi="10.1/real", pmid=None, pmcid=None, now="t").key
    for name in ("kept.figures.pdf", "scan.pdf"):
        shutil.copy(pdf, lib / "papers" / name)
    conn.execute("UPDATE papers SET figures_file = 'kept.figures.pdf' WHERE key = ?", (paper,))
    stub = file_paper(conn, title="its bytes", file="scan.pdf", sha256=sha256_of(pdf), fmt="pdf", doi=None, pmid=None, pmcid=None, now="t").key
    a = _saved(lib, conn, _panel(lib, conn, stub, 1, WHOLE, "Figure 1. Turbidity over time.", file="scan.pdf"), "a")["id"]
    out = figures.keep_beside(conn, lib / "papers", paper, pdf, "t")
    conn.commit()
    assert out["same"] and [r[0] for r in conn.execute("SELECT key FROM papers")] == [paper]
    assert [(s["id"], s["paper"], s["figure_label"], len(s["points"])) for s in digitise.orphans(conn, paper)] == [(a, paper, "1", 8)]


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


def test_a_library_of_more_figures_than_a_statement_binds_is_listed(tmp_path):
    """SQLite binds at most 32766 values in one statement (999 before 3.32): the default listing,
    every figure of the library, is past that in a large one."""
    lib, conn, key, fig = _library(tmp_path)
    more = 33_000
    conn.executemany("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, ancestry, text) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     [(f"{key}#extra-{i}", key, None, 10 + i, 1, "picture", "picture", "results", "[]", "") for i in range(more)])
    conn.commit()
    got = digitise.find(conn, "", limit=5)
    assert got["total"] == more + 1 and len(got["figures"]) == 5
    assert got["figures"][0]["figure"] == fig and "Turbidity" in got["figures"][0]["caption"]  # the one with a caption that speaks of a trend, first
    assert [f["figure"] for f in digitise.find(conn, "turbidity")["figures"]] == [fig]


def test_a_figure_is_cut_from_its_pdf_on_the_ingest_thread_and_no_other(tmp_path, monkeypatch):
    """pdfium is not safe to call from two threads, whatever documents they hold: the worker's
    rule is that the ingest thread alone calls it. A figure opened in the Plots tab is cut there
    too, behind whatever is being read, and its answer closes the request."""
    import threading
    import time

    from litrag_parser import worker as w
    from litrag_parser.library import create_library

    assert create_library(tmp_path, "lib").dir == tmp_path / "lib"
    _, conn, key, fig = _library(tmp_path)
    conn.close()
    out, cut_on = [], []
    monkeypatch.setattr(w, "emit", out.append)
    cut = digitise._cut
    monkeypatch.setattr(digitise, "_cut", lambda *a: (cut_on.append(threading.current_thread().name), cut(*a))[1])
    worker = w.Worker(tmp_path)
    threading.Thread(target=worker.ingest_loop, daemon=True, name="ingest").start()
    worker.answer({"id": "r1", "op": "digitise_open", "lib": "lib", "figure": fig})
    until = time.time() + 120
    while not out and time.time() < until:
        time.sleep(0.05)
    assert cut_on == ["ingest"]
    assert [(m.get("event"), m["id"]) for m in out] == [("digitise", "r1")] and Path(out[0]["image"]["path"]).is_file()


def test_an_image_file_s_export_names_the_file_not_where_it_lay(tmp_path):
    item = {"series": "a", "calibration": CAL, "points": PTS, "image": {"width": 600, "height": 450, "scale": 1.0, "sha256": "ab", "path": "/home/someone/plots/digitised/scan.png"}}
    ex = digitise.answer("digitise_export", {"path": str(tmp_path / "out.csv"), "source": {"file": "/home/someone/plots/scan.jpg"}, "series": [item]}, None)
    text = Path(ex["files"][0]["sidecar"]).read_text()
    assert json.loads(text)["figure"]["file"] == "scan.jpg" and "someone" not in text


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
    sv = digitise.answer("digitise_save", {"lib": "lib", "figure": fig, "series": "A", "calibration": opened["auto"]["calibration"], "points": ex["points"], "colour": s["colour"], "image": opened["image"]}, get)
    assert sv["event"] == "digitised" and sv["series"]["series"] == "A"
    ft = digitise.answer("fit", {"lib": "lib", "series_id": sv["series"]["id"]}, get)
    assert ft["event"] == "fit" and set(ft["fits"]) == {"logistic", "gompertz"} and ft["preferred"] in ("logistic", "gompertz")
    again = digitise.answer("digitise_open", {"lib": "lib", "figure": fig}, get)["saved"][0]["fits"]
    assert set(again) == {"logistic", "gompertz"}
    for model, f in ft["fits"].items():  # read back, a fit is the one that was answered: its curve too, to draw
        assert len(f["curve"]) > 10 and again[model]["curve"] == f["curve"] and again[model]["derived"] == f["derived"]
    ex2 = digitise.answer("digitise_export", {"lib": "lib", "ids": [sv["series"]["id"]], "path": str(tmp_path / "a.csv")}, get)
    assert ex2["event"] == "exported" and Path(ex2["files"][0]["sidecar"]).is_file() and len(ex2["files"][0]["fits"]) == 2
    found = digitise.answer("plots", {"lib": "lib", "query": "turbidity"}, get)
    assert found["event"] == "plots" and found["figures"][0]["digitised"] == 1
    assert digitise.answer("digitise_delete", {"lib": "lib", "series_id": sv["series"]["id"]}, get)["deleted"] is True


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
    assert fit.both(list(zip(t, _curve("logistic", t, 0.005))), ["gompertz"])["preferred"] == "gompertz"  # of the ones asked for
    one = digitise.answer("fit", {"points": [[float(a), float(b)] for a, b in zip(t, _curve("gompertz", t, 0.005))], "model": "logistic"}, None)
    assert set(one["fits"]) == {"logistic"} and one["preferred"] == "logistic"
    assert digitise.answer("fit", {"points": [[float(a), float(b)] for a, b in zip(t, _curve("gompertz", t, 0.005))]}, None)["preferred"] == "gompertz"
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
    # a unit is a person's words: a comma in one stays in its cell
    by = {r[0]: r for r in csv.reader(fit.params_csv(f, "min", "a.u., 400 nm").splitlines()) if r}
    assert by["plateau"][4] == "a.u., 400 nm" and by["max_rate"][4] == "a.u., 400 nm/min" and all(len(by[q]) == 5 for q in fit.DERIVED)


def test_points_that_show_only_a_plateau_say_the_lag_cannot_be_told():
    t = np.linspace(40, 200, 40)
    y = 0.52 - 0.15 * np.exp(-(t - 40) / 12)  # the end of a rise, no baseline
    f = fit.fit(list(zip(t, y)), "logistic")
    assert f["status"] == "fit" and "cannot be told" in (f["reason"] or "")


def test_a_steep_rise_drawn_in_pieces_is_followed_and_keeps_its_points():
    img = np.full((300, 400, 3), 255, np.uint8)
    xs = np.arange(20, 380)
    ys = (250 - 200 / (1 + np.exp(-(xs - 200) / 4))).astype(int)  # a near-vertical rise at x = 200
    for x, y in zip(xs, ys):
        img[y - 1 : y + 2, x] = (167, 0, 225)
    for x0, x1 in zip(xs[:-1], xs[1:]):  # the vertical joins, every other one broken: a rise drawn in pieces
        a, b = sorted((int(ys[x0 - 20]), int(ys[x1 - 20])))
        if (x0 // 2) % 2:
            img[a:b + 1, x1] = (167, 0, 225)
    got = digitise.extract_line(digitise.colour_mask(img, (167, 0, 225)))
    assert got[0][0] < 30 and got[-1][0] > 370
    assert sum(80 <= y <= 220 for _, y in got) >= 4  # points on the rise itself, not only either side of it
