import { describe, expect, it } from 'vitest';
import { Calibration, addPoint, charted, deletePoint, exportName, fitRows, fitSvg, hex, movePoint, nearest, nextName, num, onPicture, tryCalibration, valued, type FitResult, type Pt } from '../src/renderer/digitise.ts';

const square = { x: { p1: [100, 400] as [number, number], v1: 0, p2: [500, 400] as [number, number], v2: 60, log: false }, y: { p1: [100, 400] as [number, number], v1: 0, p2: [100, 100] as [number, number], v2: 0.8, log: false } };

describe('the calibration, as the worker makes it', () => {
  it('maps pixels to values on linear axes', () => {
    const c = new Calibration(square);
    const [x, y] = c.value(300, 250);
    expect(x).toBeCloseTo(30);
    expect(y).toBeCloseTo(0.4);
    const [x2, y2] = c.value(400, 325);
    expect(x2).toBeCloseTo(45);
    expect(y2).toBeCloseTo(0.2);
  });

  it('maps a turned plot as well as a square one', () => {
    const a = (4 * Math.PI) / 180;
    const rot = (x: number, y: number): [number, number] => [200 + (x - 200) * Math.cos(a) - (y - 300) * Math.sin(a), 300 + (x - 200) * Math.sin(a) + (y - 300) * Math.cos(a)];
    const c = new Calibration({ x: { p1: rot(100, 400), v1: 0, p2: rot(500, 400), v2: 10, log: false }, y: { p1: rot(100, 400), v1: 0, p2: rot(100, 100), v2: 3, log: false } });
    const [x, y] = c.value(...rot(300, 250));
    expect(x).toBeCloseTo(5, 9);
    expect(y).toBeCloseTo(1.5, 9);
  });

  it('works in decades on a log axis, and refuses a value at or below zero there', () => {
    const c = new Calibration({ x: { p1: [0, 100], v1: 0.01, p2: [400, 100], v2: 100, log: true }, y: { p1: [0, 100], v1: 1, p2: [0, 0], v2: 1e4, log: true } });
    const [x, y] = c.value(200, 50);
    expect(x).toBeCloseTo(1);
    expect(y).toBeCloseTo(100);
    expect(() => new Calibration({ ...square, x: { ...square.x, log: true } })).toThrow(/above zero/);
  });

  it('says what is missing, or why the points fix nothing', () => {
    expect(tryCalibration({ x: { p1: [0, 0], v1: 0 }, y: {} }).why).toMatch(/X2’s point.*Y1’s point/);
    expect(tryCalibration({ x: { ...square.x, p2: [150, 400] }, y: { ...square.y, p2: [125, 400] } }).why).toMatch(/in line/);
    expect(tryCalibration(square).cal).not.toBeNull();
  });
});

describe('a series’ points', () => {
  const pts: Pt[] = [{ px: 300, py: 250, how: 'auto' }, { px: 200, py: 300, how: 'auto' }];

  it('are valued through the calibration and kept in order of x', () => {
    const v = valued(pts, new Calibration(square));
    expect(v.map((p) => Math.round(p.x!))).toEqual([15, 30]);
    expect(valued(pts, null)[0]!.x).toBeUndefined();
  });

  it('are added, moved and deleted, each saying how it came', () => {
    let p = addPoint(pts, 10, 10);
    expect(p[2]!.how).toBe('added');
    p = movePoint(p, 0, 301, 251);
    expect(p[0]).toEqual({ px: 301, py: 251, how: 'moved' });
    expect(movePoint(p, 2, 11, 11)[2]!.how).toBe('added'); // added stays added
    expect(nearest(p, 205, 302, 8)).toBe(1);
    expect(nearest(p, 600, 600, 8)).toBe(-1);
    expect(deletePoint(p, 1).length).toBe(2);
  });
});

describe('the fit, as the window shows it', () => {
  const f: FitResult = {
    model: 'logistic', formula: 'y = …', status: 'fit', reason: null, n: 5,
    params: {},
    derived: { plateau: { value: 0.85, lo: 0.84, hi: 0.86 }, max_rate: { value: 0.05, lo: 0.049, hi: 0.051 }, t_half: { value: 25, lo: 24.9, hi: 25.1 }, lag: { value: 17, lo: 16.8, hi: 17.2 }, baseline: { value: 0.05, lo: null, hi: null } },
    r2: 0.999, residuals: [{ t: 0, y: 0.05, fit: 0.06, residual: -0.01 }, { t: 50, y: 0.85, fit: 0.84, residual: 0.01 }], curve: [[0, 0.06], [50, 0.84]],
  };

  it('is a row per quantity with its interval and unit', () => {
    const rows = fitRows(f, 'min', 'OD');
    expect(rows[0]).toEqual(['Plateau', '0.85', '0.84 – 0.86', 'OD']);
    expect(rows[1]![3]).toBe('OD/min');
    expect(rows[2]).toEqual(['Time to half maximum', '25', '24.9 – 25.1', 'min']);
    expect(rows[4]![2]).toBe('—');
  });

  it('draws the points, the curve, the lag and the residuals', () => {
    const s = fitSvg([[0, 0.05], [25, 0.45], [50, 0.85]], f, { xLabel: 'Time (min)', yLabel: 'OD' });
    expect(s.startsWith('<svg')).toBe(true);
    expect((s.match(/<circle/g) ?? []).length).toBe(3 + 2);
    expect(s).toContain('<polyline');
    expect(s).toContain('>lag<');
    expect(s).toContain('residual');
    expect(s).toContain('Time (min)');
    expect(fitSvg([], null)).toContain('<svg');
  });
});

describe('small things', () => {
  it('writes colours, numbers and file names', () => {
    expect(hex(31, 78, 156)).toBe('#1f4e9c');
    expect(num(0.123456)).toBe('0.1235');
    expect(num(1234567)).toBe('1.235e+6');
    expect(num(null)).toBe('');
    expect(exportName('doi:10.1/abc', '2', 'digitised')).toBe('doi_10.1_abc_fig2_digitised.csv');
  });
});

describe('a saved series, on the picture shown', () => {
  const saved = { calibration: square, points: [{ px: 300, py: 250, how: 'auto' as const }, { px: 400, py: 325, how: 'moved' as const }], image: { sha256: 'aaa' } };

  it('puts its points and its calibration on the image they were read from', () => {
    const on = onPicture(saved, 'aaa');
    expect(on.same).toBe(true);
    expect(on.points).toEqual(saved.points);
    expect(new Calibration(on.calibration!).value(300, 250)[0]).toBeCloseTo(30);
  });

  it('puts nothing on a picture that changed since, and leaves what was saved as it was', () => {
    const before = JSON.stringify(saved);
    const on = onPicture(saved, 'bbb');
    expect(on).toEqual({ same: false, points: [], calibration: null });
    expect(JSON.stringify(saved)).toBe(before);
  });

  it('does not take a series saved with no image, or shown on none, for one read from this picture', () => {
    expect(onPicture({ ...saved, image: {} }, 'aaa').same).toBe(false);
    expect(onPicture({ calibration: square, points: saved.points }, 'aaa').points).toEqual([]);
    expect(onPicture(saved, undefined).calibration).toBeNull();
  });
});

describe('what a fit is drawn over', () => {
  const kept: Pt[] = [{ px: 10, py: 90, how: 'auto', x: 0, y: 0.05 }, { px: 50, py: 50, how: 'auto', x: 30, y: 0.45 }, { px: 90, py: 10, how: 'moved', x: 60, y: 0.85 }];
  const fit: FitResult = { model: 'logistic', status: 'fit', formula: 'y', n: 3, params: {}, derived: {}, curve: [[0, 0.05], [30, 0.45], [60, 0.85]], residuals: [0, 0.01, -0.01] } as unknown as FitResult;

  const series = { calibration: square, points: kept, image: { sha256: 'aaa' } };

  it('is the points on the picture, when there are any', () => {
    expect(charted([{ px: 1, py: 2, how: 'auto', x: 5, y: 6 }], series, 'bbb')).toEqual([[5, 6]]);
    expect(charted([{ px: 1, py: 2, how: 'auto' }], undefined, 'bbb')).toEqual([]); // no axes yet: no values
  });

  it('is nothing when a series of this picture has had its points cleared', () => {
    expect(charted([], series, 'aaa')).toEqual([]);
  });

  it('is a kept series’ saved values when it was read from another picture, so its fit is drawn', () => {
    const pts = charted([], series, 'bbb');
    expect(pts).toEqual([[0, 0.05], [30, 0.45], [60, 0.85]]);
    const svg = fitSvg(pts, fit);
    expect(svg).toContain('<polyline');
    expect(svg.match(/<circle/g)?.length ?? 0).toBeGreaterThanOrEqual(3);
    expect(fitSvg([], fit)).not.toContain('<polyline'); // what it was: the numbers over an empty chart
  });
});

describe('the name a new series is offered', () => {
  it('counts on from the figure’s saved series', () => {
    expect(nextName([])).toBe('series 1');
    expect(nextName(['series 1'])).toBe('series 2');
    expect(nextName(['series 1', 'series 2'])).toBe('series 3');
    expect(nextName(['Collagen 37 °C'])).toBe('series 2');
  });

  it('is never a name already saved, whatever was deleted or typed before', () => {
    expect(nextName(['series 2'])).toBe('series 3'); // series 1 deleted: 2 is taken
    expect(nextName(['a', 'series 3'])).toBe('series 4');
    const taken = ['series 1', 'series 3', 'series 4'];
    expect(taken).not.toContain(nextName(taken));
  });
});
