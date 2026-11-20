"""The digitiser from a shell: what the Plots tab does, on a figure of a library or an image file.

    python -m litrag_parser.digitise_cli digitise <figure-id | image> [--lib L] [--root DIR]
        [--x PX:V,PX:V] [--y PY:V,PY:V] [--xlog] [--ylog] [--calibration FILE] [--accept-axes]
        [--colour #rrggbb] [--mode auto|markers|line] [--tolerance N] [--region X0,Y0,X1,Y1]
        [--save NAME] [--fit logistic|gompertz|both] [--csv OUT] [--json]
    python -m litrag_parser.digitise_cli fit <points.csv | figure-id> [--series NAME] [--lib L] [--id N]
        [--model logistic|gompertz|both] [--csv OUT] [--json]

The axes are a person's when given — `--x 120:0,580:60` puts x = 0 at pixel column 120 and
x = 60 at column 580; `--y` the same with pixel rows; `--calibration` a JSON calibration, such as
an export's sidecar holds — and else the ones read from the figure's numbers, taken when the
reader is confident of them, and only with `--accept-axes` when it is not: the axes used are
always printed, for a person to check. The series is found by `--colour`, else by the figure's
commonest colour that is not black or grey (or black, when it has none). `lit digitise` and
`lit fit` run this.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

from . import digitise
from . import fit as fitting
from .digitise import Calibration


def _pairs(text: str) -> list[tuple[float, float]]:
    out = []
    for part in text.split(","):
        a, sep, b = part.partition(":")
        if not sep:
            raise ValueError(f"{text!r}: give two PIXEL:VALUE pairs, e.g. 120:0,580:60")
        out.append((float(a), float(b)))
    if len(out) != 2:
        raise ValueError(f"{text!r}: give two PIXEL:VALUE pairs, e.g. 120:0,580:60")
    return out


def calibration_of(a: argparse.Namespace, auto: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    """The calibration to use, and where it came from: the person's, or the one read."""
    if a.calibration:
        d = json.loads(Path(a.calibration).read_text("utf-8"))
        if "series" in d and "x" not in d:  # an export's sidecar: its first series' calibration
            d = d["series"][0]["calibration"]
        Calibration.from_dict(d)
        return d, "given"
    if a.x or a.y:
        if not (a.x and a.y):
            raise ValueError("give both --x and --y, or neither")
        (x1, xv1), (x2, xv2) = _pairs(a.x)
        (y1, yv1), (y2, yv2) = _pairs(a.y)
        # square to the image: the x ticks on one row, the y ticks on one column
        d = {"x": {"p1": [x1, 0.0], "v1": xv1, "p2": [x2, 0.0], "v2": xv2, "log": a.xlog},
             "y": {"p1": [0.0, y1], "v1": yv1, "p2": [0.0, y2], "v2": yv2, "log": a.ylog}}
        Calibration.from_dict(d)
        return d, "given"
    if not auto or not auto.get("found"):
        raise ValueError(f"the axes could not be read ({(auto or {}).get('reason') or 'no frame'}): give them with --x and --y")
    if not auto["confident"] and not a.accept_axes:
        raise ValueError("the axes read from the figure are not sure enough to take unasked: check them below and pass --accept-axes, or give --x and --y\n"
                         + json.dumps(auto["calibration"]))
    d = auto["calibration"]
    if a.xlog != d["x"]["log"] and a.xlog:
        d["x"]["log"] = True
    if a.ylog != d["y"]["log"] and a.ylog:
        d["y"]["log"] = True
    return d, "read, confident" if auto["confident"] else "read, accepted"


def default_colour(palette: list[dict[str, Any]]) -> str:
    """The commonest colour that is a colour — neither black nor grey — else black."""
    for p in palette:
        r, g, b = digitise.rgb_of(p["colour"])
        if max(r, g, b) - min(r, g, b) >= 40:
            return p["colour"]
    return "#000000"


def cmd_digitise(a: argparse.Namespace) -> int:
    from .library import library_root, open_library
    from .store import open_store

    target = Path(a.target).expanduser()
    conn = lib = None
    if target.is_file():
        got = digitise.open_image(target, out_dir=None)
        figure = None
    else:
        if not a.lib:
            raise ValueError(f"{a.target!r} is neither an image file nor, without --lib, a figure of a library")
        lib = open_library(Path(a.root).expanduser() if a.root else library_root(), a.lib)
        if lib is None:
            raise ValueError(f"no library {a.lib!r}")
        conn = open_store(lib.store_path)
        got = digitise.render(lib.dir, conn, a.target)
        figure = a.target
        if got["image"] is None:
            raise ValueError(f"the figure cannot be drawn: {got['source'].get('reason')}")
    try:
        img = got.pop("_img")
        words = got.pop("_words", None)
        auto = got["auto"]
        cal_d, how = calibration_of(a, auto)
        cal = Calibration.from_dict(cal_d)
        colour = a.colour or default_colour(got["palette"])
        region = [float(v) for v in a.region.split(",")] if a.region else (auto or {}).get("plot_region")
        ex = digitise.extract(img, colour, tol=a.tolerance, region=region, mode=a.mode, calibration=cal, words=words)
        axes = {ax: {"label": (auto or {}).get(ax, {}).get("label"), "unit": (auto or {}).get(ax, {}).get("unit")} for ax in ("x", "y")}
        method = {"mode": ex["mode"], "asked": a.mode, "tolerance": a.tolerance, "region": ex["region"], "colour": ex["colour"], "auto_axes": how.startswith("read"), "by": "lit digitise"}
        series = {"series": a.save or "series", "colour": ex["colour"], "calibration": cal.to_dict(), "points": ex["points"], "method": method, "image": got["image"], "axes": axes}
        fits: dict[str, Any] = {}
        if a.fit:
            models = list(fitting.MODELS) if a.fit == "both" else [a.fit]
            fits = {m: fitting.fit([(p["x"], p["y"]) for p in ex["points"]], m) for m in models}
        saved = None
        if a.save:
            if conn is None:
                raise ValueError("--save keeps a series with a figure of a library; an image file's points go to --csv")
            saved = digitise.save(conn, figure, a.save, cal.to_dict(), ex["points"], colour=ex["colour"], method=method, image=got["image"], axes=axes)
            for f in fits.values():
                digitise.save_fit(conn, saved["id"], f)
            saved = digitise.one(conn, saved["id"])
        exported = None
        if a.csv:
            if saved is not None:
                exported = digitise.export(conn, [saved["id"]], Path(a.csv), library=lib.id if lib else None)
            else:
                item = digitise.unsaved({**series, "fits": fits}, {"file": str(target) if figure is None else None})
                if figure is not None:
                    item.update(paper=got["source"]["paper"], figure=figure, figure_label=got["source"].get("label"), page=got["source"].get("page"), bbox=got["source"].get("bbox"))
                exported = digitise.export(conn, [], Path(a.csv), library=lib.id if lib else None, inline=[item])
        out = {"source": got["source"], "image": got["image"], "axes_from": how, "calibration": cal.to_dict(), "axes": axes, "colour": ex["colour"], "mode": ex["mode"],
               "legend_left_out": ex["legend"], "points": ex["points"], "fits": fits, "saved": saved["id"] if saved else None, "exported": exported, "palette": got["palette"]}
    finally:
        if conn is not None:
            conn.close()
    if a.json:
        json.dump(out, sys.stdout, indent=1, default=float)
        print()
        return 0
    print(f"# axes {how}: x {cal_d['x']['v1']}→{cal_d['x']['v2']}{' (log)' if cal_d['x']['log'] else ''} at pixels {cal_d['x']['p1']}, {cal_d['x']['p2']}; "
          f"y {cal_d['y']['v1']}→{cal_d['y']['v2']}{' (log)' if cal_d['y']['log'] else ''} at {cal_d['y']['p1']}, {cal_d['y']['p2']}", file=sys.stderr)
    print(f"# {len(ex['points'])} points found as {ex['mode']} by {ex['colour']}" + (f"; {ex['legend']} legend marker(s) left out" if ex["legend"] else ""), file=sys.stderr)
    if exported:
        print(f"# wrote {exported['csv']} and {exported['sidecar']}" + (f" and {', '.join(exported['fits'])}" if exported["fits"] else ""), file=sys.stderr)
    else:
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow([digitise._head(axes["x"]["label"], axes["x"]["unit"], "x"), digitise._head(axes["y"]["label"], axes["y"]["unit"], "y")])
        for p in ex["points"]:
            w.writerow([p["x"], p["y"]])
    for m, f in fits.items():
        _print_fit(f, axes["x"]["unit"], axes["y"]["unit"])
    return 0


def read_points(path: Path) -> tuple[list[tuple[float, float]], str | None, str | None]:
    """The (x, y) pairs of a CSV: the first two columns that hold numbers (a `series` column first
    is passed over), and the units its header names in parentheses."""
    rows = list(csv.reader(path.read_text("utf-8-sig").splitlines()))
    head = rows[0] if rows and not _numeric(rows[0]) else None
    body = rows[1:] if head else rows
    cols = None
    pts = []
    for r in body:
        nums = [i for i, c in enumerate(r) if _num(c) is not None]
        if len(nums) < 2:
            continue
        cols = cols or nums[:2]
        pts.append((_num(r[cols[0]]), _num(r[cols[1]])))
    units = [digitise.charts.unit_of(head[c]) if head and cols and c < len(head) else None for c in (cols or [0, 1])]
    return [(float(x), float(y)) for x, y in pts], units[0], units[1]


def _num(c: str) -> float | None:
    try:
        v = float(c)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _numeric(row: list[str]) -> bool:
    return sum(_num(c) is not None for c in row) >= 2


def _print_fit(f: dict[str, Any], xu: str | None, yu: str | None) -> None:
    print(f"\n{f['model']}: {f['formula']}", file=sys.stderr)
    if f["status"] != "fit":
        print(f"  not fitted: {f['reason']}", file=sys.stderr)
        return
    units = {"plateau": yu, "baseline": yu, "max_rate": f"{yu or 'y'}/{xu or 't'}", "t_half": xu, "lag": xu}
    for name in fitting.DERIVED:
        d = f["derived"][name]
        ci = f"[{d['lo']:.4g}, {d['hi']:.4g}]" if d["lo"] is not None else "[—]"
        print(f"  {name:9} {d['value']:>11.4g}  95% {ci:24} {units[name] or ''}", file=sys.stderr)
    print(f"  n = {f['n']}, R² = {f['r2']}, RMSE = {f['rmse']}" + (f"; {f['reason']}" if f.get("reason") else ""), file=sys.stderr)


def cmd_fit(a: argparse.Namespace) -> int:
    from .library import library_root, open_library
    from .store import open_store

    src = Path(a.source).expanduser() if a.source else None
    xu = yu = None
    did = a.id
    conn = None
    if src is not None and src.is_file():
        pts, xu, yu = read_points(src)
    else:
        if not a.lib:
            raise ValueError("give a CSV of points, or --lib with a figure and --series, or --lib with --id")
        lib = open_library(Path(a.root).expanduser() if a.root else library_root(), a.lib)
        if lib is None:
            raise ValueError(f"no library {a.lib!r}")
        conn = open_store(lib.store_path)
        if did is None:
            saved = digitise.of_figure(conn, a.source)
            pick = [s for s in saved if a.series is None or s["series"] == a.series]
            if len(pick) != 1:
                raise ValueError(f"{len(pick)} saved series on {a.source!r}{' named ' + repr(a.series) if a.series else ''}: name one with --series ({', '.join(s['series'] for s in saved) or 'none saved'})")
            did = pick[0]["id"]
        s = digitise.one(conn, int(did))
        pts = [(p["x"], p["y"]) for p in s["points"]]
        xu, yu = s["axes"]["x"]["unit"], s["axes"]["y"]["unit"]
    models = list(fitting.MODELS) if a.model == "both" else [a.model]
    fits = {m: fitting.fit(pts, m) for m in models}
    if conn is not None:
        for f in fits.values():
            digitise.save_fit(conn, int(did), f)
        conn.close()
    if a.csv:
        out = Path(a.csv)
        for m, f in fits.items():
            p = out if len(fits) == 1 else out.with_name(f"{out.stem}.{m}{out.suffix or '.csv'}")
            p.write_text(fitting.params_csv(f, xu, yu), encoding="utf-8")
            print(f"# wrote {p}", file=sys.stderr)
    scored = [(f["aic"], m) for m, f in fits.items() if f["status"] == "fit" and f.get("aic") is not None]
    if a.json:
        json.dump({"fits": fits, "preferred": min(scored)[1] if scored else None, "units": {"x": xu, "y": yu}}, sys.stdout, indent=1)
        print()
        return 0
    for f in fits.values():
        _print_fit(f, xu, yu)
    if len(scored) > 1:
        print(f"\nthe AIC prefers {min(scored)[1]}", file=sys.stderr)
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="lit", description="Digitise a plot, and fit a sigmoid to its points.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("digitise", help="a figure of a library, or an image file, read back into (x, y) points")
    d.add_argument("target", help="a figure's node id (with --lib), or an image file")
    d.add_argument("--lib")
    d.add_argument("--root")
    d.add_argument("--x", help="two x ticks as PIXEL_COLUMN:VALUE, e.g. 120:0,580:60")
    d.add_argument("--y", help="two y ticks as PIXEL_ROW:VALUE, e.g. 410:0,40:0.8")
    d.add_argument("--xlog", action="store_true")
    d.add_argument("--ylog", action="store_true")
    d.add_argument("--calibration", help="a JSON calibration, or an export's sidecar")
    d.add_argument("--accept-axes", action="store_true", help="take the axes read from the figure even when the reader is not sure of them")
    d.add_argument("--colour", "--color", dest="colour")
    d.add_argument("--mode", default="auto", choices=("auto", "markers", "line"))
    d.add_argument("--tolerance", type=float, default=digitise.TOLERANCE)
    d.add_argument("--region", help="X0,Y0,X1,Y1 in pixels: where to look")
    d.add_argument("--save", metavar="NAME", help="keep the series with the figure, under this name")
    d.add_argument("--fit", choices=("logistic", "gompertz", "both"))
    d.add_argument("--csv", help="write the points here, a JSON sidecar beside them")
    d.add_argument("--json", action="store_true")
    f = sub.add_parser("fit", help="a sigmoid fitted to points: a CSV, or a saved series")
    f.add_argument("source", nargs="?", help="a CSV of x, y; or a figure's node id with --lib")
    f.add_argument("--series")
    f.add_argument("--lib")
    f.add_argument("--root")
    f.add_argument("--id", type=int, help="a saved series' id")
    f.add_argument("--model", default="both", choices=("logistic", "gompertz", "both"))
    f.add_argument("--csv", help="write the fit's quantities here")
    f.add_argument("--json", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        return cmd_digitise(a) if a.cmd == "digitise" else cmd_fit(a)
    except ValueError as e:
        print(f"lit {a.cmd}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
