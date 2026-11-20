"""How well digitise.py reads a plot back into numbers, against plots whose numbers are known.

Two kinds of truth:

- **Synthetic** — `tests/fixtures/digitise` (made by its `make.py`): plots drawn from known data,
  linear and log axes, markers and lines, a black series beside black axes, gridlines, a noisy
  JPEG scan. Each is read as an image (its words by OCR) and, where there is one, as a PDF (its
  words from the text layer).
- **Real** — figures of a library, with points a person measured by hand: a JSON file of
  `[{lib, figure, colour, mode, calibration, points: [[x, y], ...]}]` (`--real FILE`), kept with
  the library, outside the repository, as the papers are. The calibration is the person's, in
  the pixels of the image `render` makes; so is the colour.

For each plot the benchmark asks what a person would: the axes the machine offers (`auto_axes`)
— taken when confident, else the person's own — and then the series by its colour (`extract`,
mode `auto`). It reports:

- **axes** — whether an offer was made and was confident, and its worst error across the plot,
  as a share of each axis's range (in decades on a log axis);
- **points** — markers: the share of the true points found within 2% of the ranges (`recall`),
  the extra points found (`extra`), and the error of those found (`mean`, `max`); lines: the
  error of each point found against the true curve at its x, and the share of the curve's x
  range the points cover.

    uv run --project parser --no-sync python -m litrag_parser.digitise_bench [--real FILE --root DIR] [--json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from . import digitise
from .digitise import Calibration

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "digitise"
MATCH = 0.02  # of each axis's range: a marker found this near a true one is that one


def _t(v: float, log: bool) -> float:
    return math.log10(v) if log else v


def _span(cal: Calibration, axis: str, vals: Sequence[float]) -> float:
    a = getattr(cal, axis)
    t = [_t(v, a.log) for v in vals if (v > 0 or not a.log)]
    return (max(t) - min(t)) or 1.0


def axes_error(got: Calibration, want: Calibration, w: int, h: int, region: Sequence[float] | None = None) -> float:
    """The worst disagreement of two calibrations over the plot (its corners and centre), as a
    share of each axis's range there."""
    x0, y0, x1, y1 = region or (0, 0, w, h)
    pts = [(x0, y0), (x1, y0), (x0, y1), (x1, y1), ((x0 + x1) / 2, (y0 + y1) / 2)]
    wv = [want.value(*p) for p in pts]
    gv = [got.value(*p) for p in pts]
    sx = _span(want, "x", [v[0] for v in wv])
    sy = _span(want, "y", [v[1] for v in wv])
    worst = 0.0
    for (ax, ay), (bx, by) in zip(gv, wv):
        if (want.x.log and (ax <= 0 or bx <= 0)) or (want.y.log and (ay <= 0 or by <= 0)):
            return math.inf
        worst = max(worst, abs(_t(ax, want.x.log) - _t(bx, want.x.log)) / sx, abs(_t(ay, want.y.log) - _t(by, want.y.log)) / sy)
    return worst


def score_markers(found: Sequence[tuple[float, float]], truth: Sequence[tuple[float, float]], cal: Calibration) -> dict[str, Any]:
    """Each true point matched to the nearest found one within MATCH of the ranges."""
    lx, ly = cal.x.log, cal.y.log
    sx = _span(cal, "x", [p[0] for p in truth])
    sy = _span(cal, "y", [p[1] for p in truth])
    norm_f = [(_t(x, lx) / sx, _t(y, ly) / sy) for x, y in found if (x > 0 or not lx) and (y > 0 or not ly)]
    used: set[int] = set()
    errs: list[float] = []
    for x, y in truth:
        tx, ty = _t(x, lx) / sx, _t(y, ly) / sy
        best = None
        for i, (fx, fy) in enumerate(norm_f):
            if i in used:
                continue
            d = max(abs(fx - tx), abs(fy - ty))
            if d <= MATCH and (best is None or d < best[0]):
                best = (d, i)
        if best is not None:
            used.add(best[1])
            errs.append(best[0])
    return {"truth": len(truth), "found": len(found), "recall": round(len(errs) / max(len(truth), 1), 4), "extra": len(norm_f) - len(used),
            "mean": round(float(np.mean(errs)), 5) if errs else None, "max": round(float(np.max(errs)), 5) if errs else None}


def score_line(found: Sequence[tuple[float, float]], truth: Sequence[tuple[float, float]], cal: Calibration) -> dict[str, Any]:
    """Each found point against the true curve at its x."""
    lx, ly = cal.x.log, cal.y.log
    tx = np.array([_t(x, lx) for x, _ in truth])
    ty = np.array([_t(y, ly) for _, y in truth])
    o = np.argsort(tx)
    tx, ty = tx[o], ty[o]
    sy = float(np.ptp(ty)) or 1.0
    errs = []
    xs = []
    for x, y in found:
        if (lx and x <= 0) or (ly and y <= 0):
            continue
        u = _t(x, lx)
        if u < tx[0] or u > tx[-1]:
            continue
        errs.append(abs(float(np.interp(u, tx, ty)) - _t(y, ly)) / sy)
        xs.append(u)
    cover = (max(xs) - min(xs)) / (float(tx[-1] - tx[0]) or 1.0) if xs else 0.0
    return {"found": len(found), "cover": round(cover, 4), "mean": round(float(np.mean(errs)), 5) if errs else None,
            "p95": round(float(np.percentile(errs, 95)), 5) if errs else None, "max": round(float(np.max(errs)), 5) if errs else None}


def run_one(img: np.ndarray, words: Any, truth_cal: Calibration, series: Sequence[dict[str, Any]], *, ocr: Any = None) -> dict[str, Any]:
    h, w = img.shape[:2]
    auto = digitise.auto_axes(img, words, ocr=ocr)
    words = auto.pop("_words", None) or words
    out: dict[str, Any] = {"offered": auto["found"], "confident": auto["confident"], "axes_error": None, "reason": auto.get("reason")}
    cal = truth_cal
    region = auto.get("plot_region")
    if auto["found"]:
        got = Calibration.from_dict(auto["calibration"])
        out["axes_error"] = round(axes_error(got, truth_cal, w, h, region), 5)
        if auto["confident"]:
            cal = got
    if region is None:
        xs = [truth_cal.x.p1[0], truth_cal.x.p2[0]]
        ys = [truth_cal.y.p1[1], truth_cal.y.p2[1]]
        region = [min(xs) - 0.05 * w, min(ys) - 0.1 * h, max(xs) + 0.05 * w, max(ys) + 0.02 * h]
    out["used"] = "offered" if cal is not truth_cal else "person"
    out["series"] = []
    for s in series:
        got = digitise.extract(img, s["colour"], region=region, calibration=cal, words=words)
        pts = [(p["x"], p["y"]) for p in got["points"]]
        truth = list(zip(s["x"], s["y"]))
        score = score_markers(pts, truth, cal) if s["kind"] == "markers" else score_line(pts, truth, cal)
        out["series"].append({"name": s["name"], "kind": s["kind"], "mode": got["mode"], **score})
    return out


def synthetic(fixtures: Path = FIXTURES, *, ocr: Any = None) -> dict[str, Any]:
    import pypdfium2 as pdfium

    from . import figures

    truth = json.loads((fixtures / "truth.json").read_text())
    results = {}
    for name, t in truth.items():
        cal = Calibration.from_dict(t["calibration"])
        img = digitise.load_image(fixtures / t["image"])
        results[f"{name}/image"] = run_one(img, None, cal, t["series"], ocr=ocr)
        if t.get("pdf"):
            pdf = pdfium.PdfDocument(str(fixtures / t["pdf"]))
            try:
                pw, ph = pdf[0].get_size()
                vimg, words, _, scale = figures.crop(pdf, 1, (0, 0, pw, ph))
            finally:
                pdf.close()
            vimg, words, scale = digitise._fit_side(np.ascontiguousarray(vimg[..., :3]), words, scale)
            f = scale * 72 / 150  # the PDF's pixels over the PNG's (made at 150 dpi)
            c = t["calibration"]
            scaled = {a: {**c[a], "p1": [v * f for v in c[a]["p1"]], "p2": [v * f for v in c[a]["p2"]]} for a in ("x", "y")}
            results[f"{name}/pdf"] = run_one(vimg, words, Calibration.from_dict(scaled), t["series"], ocr=None)
    return results


def score_curve(found: Sequence[tuple[float, float]], truth: Sequence[tuple[float, float]], cal: Calibration) -> dict[str, Any]:
    """Each found point's distance to the curve a person traced (the polyline through their
    points), each axis scaled by its range — for a real line, whose steep stretches a person
    traces no better in x than in y."""
    lx, ly = cal.x.log, cal.y.log
    sx = _span(cal, "x", [p[0] for p in truth])
    sy = _span(cal, "y", [p[1] for p in truth])
    pts = sorted((_t(x, lx) / sx, _t(y, ly) / sy) for x, y in truth)
    segs = list(zip(pts, pts[1:]))
    errs, xs = [], []
    for x, y in found:
        if (lx and x <= 0) or (ly and y <= 0):
            continue
        u, v = _t(x, lx) / sx, _t(y, ly) / sy
        if u < pts[0][0] - 0.01 or u > pts[-1][0] + 0.01:
            continue
        best = math.inf
        for (ax, ay), (bx, by) in segs:
            dx, dy = bx - ax, by - ay
            k = max(0.0, min(1.0, ((u - ax) * dx + (v - ay) * dy) / (dx * dx + dy * dy or 1e-12)))
            best = min(best, math.hypot(u - ax - k * dx, v - ay - k * dy))
        errs.append(best)
        xs.append(u)
    cover = (max(xs) - min(xs)) / ((pts[-1][0] - pts[0][0]) or 1.0) if xs else 0.0
    return {"found": len(found), "cover": round(cover, 4), "mean": round(float(np.mean(errs)), 5) if errs else None,
            "p95": round(float(np.percentile(errs, 95)), 5) if errs else None, "max": round(float(np.max(errs)), 5) if errs else None}


def real(spec: Path, root: Path) -> dict[str, Any]:
    """Each figure measured by hand, read as a person would read it: the axes offered (scored
    against the person's, not used), then the series by the colour a click on it picks
    (`pick`) or names (`colour`), inside the region over its panel, on the person's axes."""
    from .library import open_library
    from .store import open_store

    results = {}
    for item in json.loads(spec.read_text()):
        name = f"{item['figure']} · {item.get('name', 'series')}"
        lib = open_library(root, item["lib"])
        conn = open_store(lib.store_path)
        try:
            r = digitise.render(lib.dir, conn, item["figure"])
        finally:
            conn.close()
        if r["image"] is None:
            results[name] = {"error": r["source"].get("reason")}
            continue
        img = r.pop("_img")
        words = r.pop("_words", None)
        cal = Calibration.from_dict(item["calibration"])
        h, w = img.shape[:2]
        region = item.get("region")
        auto = digitise.auto_axes(img, digitise.load_words(r["image"]["path"], whole=True),
                                  near=[(region[0] + region[2]) / 2, (region[1] + region[3]) / 2] if region else None)
        auto.pop("_words", None)
        out: dict[str, Any] = {"offered": auto.get("found", False), "confident": auto.get("confident", False), "axes_error": None, "used": "person"}
        if auto.get("found"):
            out["axes_error"] = round(axes_error(Calibration.from_dict(auto["calibration"]), cal, w, h, region), 5)
        colour = item.get("colour") or digitise.colour_at(img, *item["pick"])
        got = digitise.extract(img, colour, region=region or auto.get("plot_region"), calibration=cal, mode=item.get("mode", "auto"),
                               tol=item.get("tolerance", digitise.TOLERANCE), words=words)
        pts = [(p["x"], p["y"]) for p in got["points"]]
        truth = [tuple(p) for p in item["points"]]
        kind = item.get("kind", "markers")
        score = score_markers(pts, truth, cal) if kind == "markers" else score_curve(pts, truth, cal)
        out["series"] = [{"name": item.get("name", "series"), "kind": kind, "mode": got["mode"], "colour": colour, **score}]
        out["paper"] = r["source"]["paper"]
        out["label"] = r["source"].get("label")
        results[name] = out
    return results


def table(results: dict[str, Any]) -> str:
    lines = [f"{'plot':38} {'axes':>14} {'series':22} {'mode':8} {'recall/cover':>12} {'mean':>8} {'max':>8} {'extra':>5}"]
    for name, r in results.items():
        if "error" in r:
            lines.append(f"{name:38} error: {r['error']}")
            continue
        ax = ("confident " if r["confident"] else "offered " if r["offered"] else "none") + (f"{r['axes_error']:.4f}" if r["axes_error"] is not None else "")
        for s in r["series"]:
            rc = s.get("recall", s.get("cover"))
            lines.append(f"{name:38} {ax:>14} {s['name'][:22]:22} {s['mode']:8} {rc:>12} {s['mean'] if s['mean'] is not None else '-':>8} {s['max'] if s['max'] is not None else '-':>8} {s.get('extra', ''):>5}")
            name, ax = "", ""
    return "\n".join(lines)


def summary(results: dict[str, Any]) -> dict[str, Any]:
    ser = [s for r in results.values() if "series" in r for s in r["series"]]
    mk = [s for s in ser if s["kind"] == "markers"]
    ln = [s for s in ser if s["kind"] == "line"]
    ax = [r["axes_error"] for r in results.values() if r.get("axes_error") is not None]
    return {
        "plots": len(results), "axes_offered": sum(1 for r in results.values() if r.get("offered")), "axes_confident": sum(1 for r in results.values() if r.get("confident")),
        "axes_error_max_confident": max((r["axes_error"] for r in results.values() if r.get("confident") and r.get("axes_error") is not None), default=None),
        "axes_error_median": round(float(np.median(ax)), 5) if ax else None,
        "markers_recall": round(float(np.mean([s["recall"] for s in mk])), 4) if mk else None,
        "markers_error_mean": round(float(np.mean([s["mean"] for s in mk if s["mean"] is not None])), 5) if mk else None,
        "markers_error_max": max((s["max"] for s in mk if s["max"] is not None), default=None),
        "line_error_mean": round(float(np.mean([s["mean"] for s in ln if s["mean"] is not None])), 5) if ln else None,
        "line_error_p95_max": max((s["p95"] for s in ln if s["p95"] is not None), default=None),
        "line_cover_min": min((s["cover"] for s in ln), default=None),
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m litrag_parser.digitise_bench", description=__doc__.split("\n")[0])
    ap.add_argument("--real", type=Path, help="a JSON file of figures with points measured by hand")
    ap.add_argument("--root", type=Path, help="the library root the real figures' libraries are under")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-synthetic", action="store_true")
    a = ap.parse_args(argv)
    results: dict[str, Any] = {}
    if not a.no_synthetic:
        results |= synthetic()
    if a.real:
        from .library import library_root

        results |= {f"real:{k}": v for k, v in real(a.real, a.root or library_root()).items()}
    if a.json:
        json.dump({"results": results, "summary": summary(results)}, sys.stdout, indent=1)
        print()
    else:
        print(table(results))
        print(json.dumps(summary(results), indent=1))


if __name__ == "__main__":
    main()
