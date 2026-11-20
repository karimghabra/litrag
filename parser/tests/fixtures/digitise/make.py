"""Synthetic plots with known data, for digitise.py's tests and its benchmark — drawn the way the
papers draw kinetic curves: turbidity against time as markers and as lines, several series of
different colours, a black series beside black axes, gridlines, a log y axis and a log x axis,
and a noisy scan (blurred, speckled, saved as a lossy JPEG). Each is written as a 150-dpi image
(read with OCR) and, but for the scan, a PDF (read with its own text layer); truth.json holds the
data each series was drawn from and, for the image, the pixel of four ticks — the calibration a
person would give.

    uv run --with matplotlib python parser/tests/fixtures/digitise/make.py

matplotlib is needed only here, to make the fixtures; they are committed.
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).parent
DPI = 150
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})
rng = np.random.default_rng(7)


def sigmoid(t, y0, a, k, tm):
    return y0 + a / (1 + np.exp(-k * (t - tm)))


def ticks_px(fig, ax, xt, yt):
    """The pixels of two x ticks (on the x axis) and two y ticks (on the y axis) in the saved image."""
    fig.canvas.draw()
    h = fig.get_size_inches()[1] * DPI
    (x0, y0), (x1, _) = ax.transData.transform([(xt[0], ax.get_ylim()[0]), (xt[1], ax.get_ylim()[0])])
    (_, ya), (_, yb) = ax.transData.transform([(ax.get_xlim()[0], yt[0]), (ax.get_xlim()[0], yt[1])])
    xa = ax.transData.transform([(ax.get_xlim()[0], yt[0])])[0][0]
    return {
        "x": {"p1": [float(x0), float(h - y0)], "v1": xt[0], "p2": [float(x1), float(h - y0)], "v2": xt[1], "log": ax.get_xscale() == "log"},
        "y": {"p1": [float(xa), float(h - ya)], "v1": yt[0], "p2": [float(xa), float(h - yb)], "v2": yt[1], "log": ax.get_yscale() == "log"},
    }


def save(name, fig, ax, xt, yt, series, x_label, y_label, pdf=True, degrade=None):
    cal = ticks_px(fig, ax, xt, yt)
    png = HERE / f"{name}.png"
    fig.savefig(png, dpi=DPI)
    if pdf:
        fig.savefig(HERE / f"{name}.pdf")
    plt.close(fig)
    if degrade:
        degrade(png)
    return {name: {"image": png.name if not degrade else png.with_suffix(".jpg").name, "pdf": f"{name}.pdf" if pdf else None, "calibration": cal,
                   "x_label": x_label, "y_label": y_label, "series": series}}


truth = {}

# 1. turbidity as markers: two series, circles and squares, a little scatter
fig, ax = plt.subplots(figsize=(3.6, 2.7), dpi=DPI)
t = np.arange(0, 61, 3.0)
series = []
for name, colour, marker, k, tm in (("Collagen", "#1f4e9c", "o", 0.22, 24), ("Collagen + decorin", "#c0392b", "s", 0.15, 33)):
    y = sigmoid(t, 0.04, 0.62, k, tm) + rng.normal(0, 0.008, len(t))
    ax.plot(t, y, marker, color=colour, markersize=4.5, label=name)
    series.append({"name": name, "colour": colour, "kind": "markers", "x": t.tolist(), "y": y.tolist()})
ax.set_xlim(-2, 62)
ax.set_ylim(0, 0.8)
ax.set_xticks(range(0, 61, 10))
ax.set_yticks([0, 0.2, 0.4, 0.6, 0.8])
ax.set_xlabel("Time (min)")
ax.set_ylabel("Absorbance (313 nm)")
ax.legend(frameon=False, loc="lower right", fontsize=7)
fig.tight_layout()
truth |= save("turbidity_markers", fig, ax, (0, 60), (0, 0.8), series, "Time (min)", "Absorbance (313 nm)")

# 2. turbidity as lines: three series, one of them black beside the black axes
fig, ax = plt.subplots(figsize=(3.6, 2.7), dpi=DPI)
t = np.linspace(0, 90, 300)
series = []
for name, colour, k, tm, a in (("37 °C", "#000000", 0.20, 22, 0.55), ("30 °C", "#2e86c1", 0.12, 40, 0.50), ("25 °C", "#e67e22", 0.08, 58, 0.45)):
    y = sigmoid(t, 0.02, a, k, tm)
    ax.plot(t, y, "-", color=colour, linewidth=1.4, label=name)
    series.append({"name": name, "colour": colour, "kind": "line", "x": t.tolist(), "y": y.tolist()})
ax.set_xlim(0, 90)
ax.set_ylim(0, 0.7)
ax.set_xticks(range(0, 91, 15))
ax.set_yticks([0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7])
ax.set_xlabel("Time (min)")
ax.set_ylabel("OD (400 nm)")
ax.legend(frameon=False, loc="upper left", fontsize=7)
fig.tight_layout()
truth |= save("turbidity_lines", fig, ax, (0, 90), (0, 0.7), series, "Time (min)", "OD (400 nm)")

# 3. a log y axis: growth over days, triangles joined by a thin line of the same colour
fig, ax = plt.subplots(figsize=(3.4, 2.7), dpi=DPI)
d = np.arange(0, 15, 1.0)
y = 10 ** (1 + 3.2 / (1 + np.exp(-0.7 * (d - 6))) + rng.normal(0, 0.03, len(d)))
ax.semilogy(d, y, "^-", color="#117a65", markersize=5, linewidth=0.8)
ax.set_xlim(-0.5, 14.5)
ax.set_ylim(1, 1e5)
ax.set_xticks(range(0, 15, 2))
ax.set_xlabel("Time (days)")
ax.set_ylabel("Cells per mL")
fig.tight_layout()
truth |= save("log_y_markers", fig, ax, (0, 14), (10, 1e4), [{"name": "Cells", "colour": "#117a65", "kind": "markers", "x": d.tolist(), "y": y.tolist()}], "Time (days)", "Cells per mL")

# 4. a log x axis: a dose-response line, with grey gridlines
fig, ax = plt.subplots(figsize=(3.6, 2.7), dpi=DPI)
c = np.logspace(-2, 2, 200)
y = 5 + 90 / (1 + (0.8 / c) ** 1.3)
ax.semilogx(c, y, "-", color="#8e44ad", linewidth=1.5)
ax.grid(True, which="major", color="0.85", linewidth=0.6)
ax.set_xlim(0.01, 100)
ax.set_ylim(0, 100)
ax.set_yticks(range(0, 101, 20))
ax.set_xlabel("Concentration (µM)")
ax.set_ylabel("Inhibition (%)")
fig.tight_layout()
truth |= save("log_x_line", fig, ax, (0.01, 100), (0, 100), [{"name": "Inhibition", "colour": "#8e44ad", "kind": "line", "x": c.tolist(), "y": y.tolist()}], "Concentration (µM)", "Inhibition (%)")

# 5. a noisy scan: markers and a fitted line, blurred, speckled and saved as a lossy JPEG
fig, ax = plt.subplots(figsize=(3.6, 2.7), dpi=DPI)
t = np.arange(0, 41, 2.5)
y = sigmoid(t, 0.1, 1.1, 0.3, 18) + rng.normal(0, 0.025, len(t))
ax.plot(t, y, "o", color="#d35400", markersize=5)
ax.set_xlim(-1, 41)
ax.set_ylim(0, 1.4)
ax.set_xticks(range(0, 41, 10))
ax.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4])
ax.set_xlabel("Time (h)")
ax.set_ylabel("Turbidity")
fig.tight_layout()


def scan(png):
    from PIL import Image, ImageFilter

    im = Image.open(png).convert("RGB").filter(ImageFilter.GaussianBlur(0.8))
    a = np.asarray(im).astype(float)
    a += rng.normal(0, 6, a.shape)
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(png.with_suffix(".jpg"), quality=70)
    png.unlink()


truth |= save("noisy_scan", fig, ax, (0, 40), (0, 1.4), [{"name": "Turbidity", "colour": "#d35400", "kind": "markers", "x": t.tolist(), "y": y.tolist()}], "Time (h)", "Turbidity", pdf=False, degrade=scan)

(HERE / "truth.json").write_text(json.dumps(truth, indent=1))
print("wrote", ", ".join(truth))
