"""Sigmoid curves fitted to digitised points: a turbidity or growth curve's lag, growth and plateau.

Two models, both with a baseline `y0` under the rise:

- **logistic** — `y = y0 + A / (1 + exp(-k (t - tm)))`. Symmetric about its midpoint `tm`.
- **gompertz** — Zwietering's form, `y = y0 + A exp(-exp(mu e (lam - t) / A + 1))`, whose
  parameters are the growth rate `mu` and the lag `lam` themselves. Rises faster than it settles.

Each fit reports the same quantities, so the two can be set side by side:

- `plateau` — `y0 + A`, the level the curve settles at;
- `max_rate` — the slope at the inflection (`A k / 4` for the logistic, `mu` for the Gompertz);
- `t_half` — the time the curve is halfway from baseline to plateau;
- `lag` — where the tangent at the inflection crosses the baseline (`tm - 2/k`; `lam`);
- `baseline` — `y0`.

Every quantity carries a 95% confidence interval: the parameters' from the covariance of the
least-squares fit (`s² (JᵀJ)⁻¹`, Student's t on n − 4 degrees of freedom), the derived ones by
the delta method through the same covariance. The fit is Levenberg–Marquardt in numpy, from
starting values read off the data (its range, its midpoint crossing, its steepest stretch); no
model is asked and nothing leaves the machine. Fewer than five points, or a fit that does not
converge, is an answer with the reason and no numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import numpy as np

MODELS = ("logistic", "gompertz")
MIN_POINTS = 5
LEVEL = 0.95


@dataclass
class Model:
    name: str
    params: tuple[str, ...]
    f: Callable[[np.ndarray, np.ndarray], np.ndarray]
    derived: Callable[[np.ndarray], dict[str, float]]
    formula: str


def _logistic(t: np.ndarray, p: np.ndarray) -> np.ndarray:
    y0, a, k, tm = p
    z = np.clip(-k * (t - tm), -60, 60)
    return y0 + a / (1 + np.exp(z))


def _logistic_derived(p: np.ndarray) -> dict[str, float]:
    y0, a, k, tm = (float(v) for v in p)
    return {"baseline": y0, "plateau": y0 + a, "max_rate": a * k / 4, "t_half": tm, "lag": tm - 2 / k if k else math.nan}


def _gompertz(t: np.ndarray, p: np.ndarray) -> np.ndarray:
    y0, a, mu, lam = p
    if a == 0:
        return np.full_like(t, y0, dtype=float)
    z = np.clip(mu * math.e / a * (lam - t) + 1, -60, 60)
    return y0 + a * np.exp(-np.exp(z))


def _gompertz_derived(p: np.ndarray) -> dict[str, float]:
    y0, a, mu, lam = (float(v) for v in p)
    # halfway: exp(-exp(z)) = 1/2, so z = ln(ln 2)
    t_half = lam + (1 - math.log(math.log(2))) * a / (mu * math.e) if mu else math.nan
    return {"baseline": y0, "plateau": y0 + a, "max_rate": mu, "t_half": t_half, "lag": lam}


LOGISTIC = Model("logistic", ("y0", "A", "k", "tm"), _logistic, _logistic_derived, "y = y0 + A / (1 + exp(-k (t - tm)))")
GOMPERTZ = Model("gompertz", ("y0", "A", "mu", "lam"), _gompertz, _gompertz_derived, "y = y0 + A exp(-exp(mu e (lam - t) / A + 1))")
_BY_NAME = {m.name: m for m in (LOGISTIC, GOMPERTZ)}


# -- Student's t, without scipy ----------------------------------------------------------------------------


def _norm_ppf(p: float) -> float:
    """The standard normal quantile (Acklam's rational approximation, |error| < 1.2e-9)."""
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00)
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - lo:
        return -_norm_ppf(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _betacf(a: float, b: float, x: float) -> float:
    """The continued fraction of the incomplete beta function (Numerical Recipes' betacf)."""
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-12:
            break
    return h


def _t_cdf(t: float, df: float) -> float:
    x = df / (df + t * t)
    bt = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - math.lgamma(0.5) + (df / 2) * math.log(x) + 0.5 * math.log(1 - x)) if 0 < x < 1 else 0.0
    if x < (df / 2 + 1) / (df / 2 + 0.5 + 2):
        ib = bt * _betacf(df / 2, 0.5, x) / (df / 2)
    else:
        ib = 1 - bt * _betacf(0.5, df / 2, 1 - x) / 0.5
    return 1 - 0.5 * ib if t >= 0 else 0.5 * ib


def t_ppf(p: float, df: float) -> float:
    """Student's t quantile, by bisection on its distribution function."""
    if df <= 0:
        return math.nan
    lo, hi = -1.0, 1.0
    while _t_cdf(lo, df) > p:
        lo *= 2
    while _t_cdf(hi, df) < p:
        hi *= 2
    for _ in range(100):
        mid = (lo + hi) / 2
        if _t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# -- the fit ------------------------------------------------------------------------------------------------


def _jacobian(m: Model, t: np.ndarray, p: np.ndarray) -> np.ndarray:
    out = np.empty((len(t), len(p)))
    base = m.f(t, p)
    for i in range(len(p)):
        h = 1e-6 * max(abs(p[i]), 1e-3)
        q = p.copy()
        q[i] += h
        out[:, i] = (m.f(t, q) - base) / h
    return out


def levenberg_marquardt(m: Model, t: np.ndarray, y: np.ndarray, p0: np.ndarray, iters: int = 400) -> tuple[np.ndarray, bool]:
    """Least squares from `p0`: (parameters, converged)."""
    p = p0.astype(float).copy()
    lam = 1e-3
    r = y - m.f(t, p)
    cost = float(r @ r)
    for _ in range(iters):
        j = _jacobian(m, t, p)
        jtj = j.T @ j
        g = j.T @ r
        improved = False
        for _ in range(30):
            a = jtj + lam * np.diag(np.maximum(np.diag(jtj), 1e-12))
            try:
                step = np.linalg.solve(a, g)
            except np.linalg.LinAlgError:
                lam *= 10
                continue
            q = p + step
            rq = y - m.f(t, q)
            cq = float(rq @ rq)
            if np.isfinite(cq) and cq < cost:
                small = abs(cost - cq) <= 1e-12 * max(cost, 1e-300) or np.all(np.abs(step) <= 1e-10 * (np.abs(p) + 1e-10))
                p, r, cost = q, rq, cq
                lam = max(lam / 10, 1e-12)
                improved = True
                if small:
                    return p, True
                break
            lam *= 10
        if not improved:
            return p, lam > 1e10 or cost <= 1e-24  # no step lowers the cost: a minimum
    return p, False


def _smooth(y: np.ndarray, w: int) -> np.ndarray:
    if w <= 1 or len(y) < w:
        return y
    k = np.ones(w) / w
    pad = np.pad(y, (w // 2, w - 1 - w // 2), mode="edge")
    return np.convolve(pad, k, mode="valid")


def start(t: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """Starting values read off the points: baseline and plateau from the ends, the midpoint
    crossing, and the steepest stretch of the smoothed curve."""
    n = len(t)
    edge = max(2, n // 10)
    y0, y1 = float(np.median(y[:edge])), float(np.median(y[-edge:]))
    a = y1 - y0 if y1 != y0 else float(np.ptp(y)) or 1.0
    ys = _smooth(y, max(1, n // 12))
    half = y0 + a / 2
    above = np.flatnonzero((ys - half) * np.sign(a) >= 0)
    tm = float(t[above[0]]) if len(above) else float(np.median(t))
    slopes = np.gradient(ys, t) if n >= 3 else np.array([a / max(float(np.ptp(t)), 1e-9)])
    i = int(np.argmax(slopes * np.sign(a)))
    mu = float(slopes[i]) if slopes[i] * a > 0 else a / max(float(np.ptp(t)), 1e-9) * 2
    lam = float(t[i]) - (float(ys[i]) - y0) / mu if mu else float(t[0])
    return {"y0": y0, "A": a, "k": 4 * mu / a if a else 1.0, "tm": tm, "mu": mu, "lam": lam}


def fit(points: Sequence[Sequence[float]], model: str = "logistic") -> dict[str, Any]:
    """`points` [(t, y)] fitted to `model`: `{model, formula, status, reason, n, params: {name:
    {value, lo, hi, se}}, derived: {plateau, max_rate, t_half, lag, baseline: {value, lo, hi}},
    r2, rmse, aic, residuals: [{t, y, fit, residual}], curve: [[t, y]]}`."""
    if model not in _BY_NAME:
        raise ValueError(f"no model {model!r}: {', '.join(MODELS)}")
    m = _BY_NAME[model]
    pts = sorted((float(a), float(b)) for a, b in points if a is not None and b is not None and math.isfinite(float(a)) and math.isfinite(float(b)))
    out: dict[str, Any] = {"model": model, "formula": m.formula, "status": "unfit", "reason": None, "n": len(pts), "params": {}, "derived": {}, "level": LEVEL}
    if len(pts) < MIN_POINTS:
        out["reason"] = f"{len(pts)} points; a sigmoid of four parameters needs at least {MIN_POINTS}"
        return out
    t = np.array([a for a, _ in pts])
    y = np.array([b for _, b in pts])
    if np.ptp(t) == 0 or np.ptp(y) == 0:
        out["reason"] = "the points do not rise or fall"
        return out
    s = start(t, y)
    tries = [np.array([s[k] for k in m.params])]
    span = float(np.ptp(t))
    for f in (0.5, 2.0):  # a slope read off a few points can be far off: try it halved and doubled
        q = tries[0].copy()
        q[2] *= f
        tries.append(q)
    for frac in (0.3, 0.7):  # and a midpoint elsewhere along the curve
        q = tries[0].copy()
        if model == "logistic":
            q[3] = float(t[0]) + frac * span
        else:
            q[3] = float(t[0]) + (frac - 0.2) * span
        tries.append(q)
    best: tuple[float, np.ndarray, bool] | None = None
    for p0 in tries:
        p, ok = levenberg_marquardt(m, t, y, p0)
        r = y - m.f(t, p)
        cost = float(r @ r)
        if np.all(np.isfinite(p)) and np.isfinite(cost) and (best is None or cost < best[0]):
            best = (cost, p, ok)
    if best is None:
        out["reason"] = "the fit did not converge"
        return out
    rss, p, ok = best
    n, k = len(t), len(p)
    dof = n - k
    fitted = m.f(t, p)
    s2 = rss / dof if dof > 0 else math.nan
    j = _jacobian(m, t, p)
    try:
        cov = np.linalg.pinv(j.T @ j) * s2
    except np.linalg.LinAlgError:
        cov = np.full((k, k), math.nan)
    tq = t_ppf(0.5 + LEVEL / 2, dof) if dof > 0 else math.nan
    for i, name in enumerate(m.params):
        se = math.sqrt(cov[i, i]) if cov[i, i] >= 0 else math.nan
        out["params"][name] = _ci(float(p[i]), se, tq)
    base = m.derived(p)
    for name, v in base.items():
        grad = np.empty(k)
        for i in range(k):
            h = 1e-6 * max(abs(p[i]), 1e-3)
            q = p.copy()
            q[i] += h
            grad[i] = (m.derived(q)[name] - v) / h
        var = float(grad @ cov @ grad)
        out["derived"][name] = _ci(v, math.sqrt(var) if var >= 0 else math.nan, tq)
    tss = float(((y - y.mean()) ** 2).sum())
    out.update({
        "status": "fit", "converged": bool(ok),
        "r2": _r(1 - rss / tss) if tss else None, "rmse": _r(math.sqrt(rss / n)),
        "aic": _r(n * math.log(rss / n) + 2 * k) if rss > 0 else None,
        "residuals": [{"t": _r(a), "y": _r(b), "fit": _r(c), "residual": _r(b - c)} for a, b, c in zip(t, y, fitted)],
        "curve": [[_r(a), _r(float(b))] for a, b in zip(np.linspace(t[0], t[-1], 120), m.f(np.linspace(t[0], t[-1], 120), p))],
    })
    if not ok:
        out["reason"] = "the fit stopped before it settled: read its numbers with care"
    return out


def _ci(v: float, se: float, tq: float) -> dict[str, float | None]:
    if not math.isfinite(se) or not math.isfinite(tq):
        return {"value": _r(v), "se": None, "lo": None, "hi": None}
    return {"value": _r(v), "se": _r(se), "lo": _r(v - tq * se), "hi": _r(v + tq * se)}


def _r(v: float) -> float:
    if v == 0 or not math.isfinite(v):
        return v
    return round(v, max(0, 6 - int(math.floor(math.log10(abs(v))))))


def both(points: Sequence[Sequence[float]]) -> dict[str, Any]:
    """Both models fitted, and which the AIC prefers."""
    fits = {name: fit(points, name) for name in MODELS}
    scored = [(f["aic"], name) for name, f in fits.items() if f["status"] == "fit" and f.get("aic") is not None]
    return {"fits": fits, "preferred": min(scored)[1] if scored else None}


DERIVED = ("plateau", "max_rate", "t_half", "lag", "baseline")


def params_csv(f: dict[str, Any], x_unit: str | None = None, y_unit: str | None = None) -> str:
    """A fit as CSV: a row per quantity — its value, its interval, its unit."""
    units = {"plateau": y_unit, "baseline": y_unit, "max_rate": f"{y_unit or 'y'}/{x_unit or 't'}" if (x_unit or y_unit) else None, "t_half": x_unit, "lag": x_unit}
    lines = [f"model,{f['model']}", f"n,{f['n']}", f"r2,{'' if f.get('r2') is None else f['r2']}", f"rmse,{'' if f.get('rmse') is None else f['rmse']}", "", "quantity,value,lo,hi,unit"]

    def cell(v: Any) -> str:
        return "" if v is None else str(v)

    for name in DERIVED:
        d = f["derived"].get(name)
        if d:
            lines.append(",".join([name, cell(d["value"]), cell(d["lo"]), cell(d["hi"]), cell(units.get(name))]))
    for name, d in f["params"].items():
        lines.append(",".join([f"param:{name}", cell(d["value"]), cell(d["lo"]), cell(d["hi"]), ""]))
    return "\n".join(lines) + "\n"
