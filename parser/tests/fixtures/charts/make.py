"""Synthetic charts with known values, for charts.py's tests — drawn the way the papers draw
them: bars with error bars and significance brackets, grouped bars with a legend and a dashed
reference line, outlined bars with slanted labels, points joined by lines over days, two panels
side by side, a log axis. Each is written as a 300-dpi PNG (read with OCR) and a PDF (rendered
and read with its own text layer), with the values it was drawn from in truth.json.

    uv run --with matplotlib python parser/tests/fixtures/charts/make.py

matplotlib is needed only here, to make the fixtures; they are committed.
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).parent
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})


def bars(ax, cats, series, colors, err, edge=None, width=0.8, capsize=4, ecolor="0.35"):
    n = len(series)
    w = width / n
    x = np.arange(len(cats))
    for i, (name, vals) in enumerate(series.items()):
        ax.bar(x - width / 2 + w * (i + 0.5), vals, w, yerr=err[name], capsize=capsize, color=colors[i], edgecolor=edge, linewidth=1 if edge else 0, label=name, error_kw={"ecolor": ecolor, "elinewidth": 1})
    ax.set_xticks(x, cats)


def truth_bars(cats, series, err, ylabel, panel=None, scale="linear"):
    return {"kind": "bar", "panel": panel, "y_label": ylabel, "y_scale": scale, "categories": cats,
            "series": [{"name": k, "values": list(map(float, v)), "errors": list(map(float, err[k]))} for k, v in series.items()]}


def save(name, fig, plots):
    fig.savefig(HERE / f"{name}.png", dpi=300)
    fig.savefig(HERE / f"{name}.pdf")
    plt.close(fig)
    return {name: plots}


truth = {}

# 1. four bars, greys, error bars with caps, a bracket and stars
fig, ax = plt.subplots(figsize=(3.2, 2.6))
cats = ["COL/PLA_Dry", "COL/PLA_Wet", "PLA_Dry", "PLA_Wet"]
s = {"Maximum load": [354.0, 267.0, 314.0, 324.0]}
e = {"Maximum load": [36.0, 13.0, 58.0, 23.0]}
bars(ax, cats, s, ["black"], e, width=0.62)
for b in ax.patches:
    b.set_color(["#000000", "#595959", "#a6a6a6", "#d9d9d9"][ax.patches.index(b)])
ax.set_ylim(0, 400)
ax.set_yticks(range(0, 401, 50))
ax.set_ylabel("Maximum Load (N)")
ax.tick_params(axis="x", labelsize=7)
ax.plot([0, 0, 1, 1], [395, 398, 398, 395], color="black", lw=0.8)
ax.text(0.5, 399, "*", ha="center")
ax.text(1, 290, "**", ha="center")
fig.tight_layout()
truth |= save("bars_simple", fig, [truth_bars(cats, s, e, "Maximum Load (N)")])

# 2. grouped bars, legend outside, dashed reference line, italic categories
fig, ax = plt.subplots(figsize=(4.6, 2.6))
cats = ["Scx", "Tnmd", "Tnc", "Col-I", "Mkx", "Sox9"]
s = {"TCP": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0], "Circle 50": [1.72, 0.75, 2.39, 0.71, 2.25, 3.41], "Rhombus 50": [1.64, 0.58, 1.37, 0.92, 1.32, 5.51]}
e = {"TCP": [0, 0, 0, 0, 0, 0], "Circle 50": [0.92, 0.2, 1.25, 0.4, 0.92, 1.05], "Rhombus 50": [0.62, 0.11, 0.06, 0.48, 0.41, 0.7]}
bars(ax, cats, s, ["black", "0.45", "0.85"], e, edge="black", ecolor="black")
ax.axhline(1, ls="--", color="black", lw=0.7)
ax.set_ylim(0, 8)
ax.set_ylabel("Fold change relative to TCP")
ax.set_xticks(range(6), cats, style="italic")
ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(1.0, 1.0))
fig.tight_layout()
truth |= save("bars_grouped", fig, [truth_bars(cats, s, e, "Fold change relative to TCP")])

# 3. outlined light bars, slanted labels, the y axis drawn without the zero line
fig, ax = plt.subplots(figsize=(2.2, 2.8))
cats = ["Circle 50", "Rhombus 50", "TCP"]
s = {"FA length": [8.1, 7.4, 16.4]}
e = {"FA length": [3.0, 2.0, 6.2]}
bars(ax, cats, s, ["0.85"], e, edge="black", width=0.65, ecolor="black")
ax.patches[1].set_facecolor("0.55")
ax.patches[2].set_facecolor("0.25")
ax.set_ylim(0, 25)
ax.set_ylabel("FA length (µm)")
plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
fig.tight_layout()
truth |= save("bars_outlined", fig, [truth_bars(cats, s, e, "FA length (µm)")])

# 4. points joined by lines, error bars, numeric x, legend inside
fig, ax = plt.subplots(figsize=(3.2, 2.6))
days = [1, 7, 14, 28]
pts = {"COL/PLA": ([0.9, 3.2, 20.6, 37.1], [0.3, 0.5, 7.5, 3.4]), "PLA": ([0.6, 1.5, 3.6, 24.2], [0.2, 0.4, 0.6, 4.9])}
for (name, (v, err)), c in zip(pts.items(), ["0.4", "0.75"]):
    ax.errorbar(days, v, yerr=err, color=c, marker="o", ms=6, lw=2, capsize=3, label=name)
ax.set_xlim(0, 30)
ax.set_ylim(0, 60)
ax.set_xlabel("Days of Culture")
ax.set_ylabel("alamarBlue Fluorescence (%)")
ax.legend(frameon=False, loc="upper left")
fig.tight_layout()
truth |= save("points_days", fig, [{"kind": "point", "panel": None, "y_label": "alamarBlue Fluorescence (%)", "x_label": "Days of Culture", "y_scale": "linear",
                                    "series": [{"name": k, "x": days, "values": v, "errors": err} for k, (v, err) in pts.items()]}])

# 5. two panels side by side, (A) and (B), each its own axis range
fig, (a1, a2) = plt.subplots(1, 2, figsize=(5.2, 2.4))
c2 = ["Random", "ELAC"]
sa, ea = {"Modulus": [0.78, 1.36]}, {"Modulus": [0.1, 0.12]}
sb, eb = {"Strength": [12.5, 31.0]}, {"Strength": [2.1, 4.4]}
bars(a1, c2, sa, ["0.3"], ea, width=0.5)
bars(a2, c2, sb, ["0.6"], eb, width=0.5)
a1.set_ylim(0, 2)
a1.set_ylabel("Young's modulus (MPa)")
a2.set_ylim(0, 40)
a2.set_ylabel("UTS (MPa)")
a1.set_title("(A)", loc="left", x=-0.3)
a2.set_title("(B)", loc="left", x=-0.3)
fig.tight_layout()
truth |= save("panels_two", fig, [truth_bars(c2, sa, ea, "Young's modulus (MPa)", panel="A"), truth_bars(c2, sb, eb, "UTS (MPa)", panel="B")])

# 6. a log axis
fig, ax = plt.subplots(figsize=(3.0, 2.6))
cats = ["Day 1", "Day 3", "Day 7"]
s = {"Cells": [1200.0, 8500.0, 52000.0]}
e = {"Cells": [300.0, 1500.0, 9000.0]}
bars(ax, cats, s, ["0.5"], e, width=0.55)
ax.set_yscale("log")
ax.set_ylim(100, 100000)
ax.set_ylabel("Cell number")
fig.tight_layout()
truth |= save("bars_log", fig, [truth_bars(cats, s, e, "Cell number", scale="log")])

(HERE / "truth.json").write_text(json.dumps(truth, indent=1))
print("wrote", ", ".join(truth))
