import { describe, expect, it } from 'vitest';
import { csvOf, fmt, plotTable, plotTitle, withError, type Plot } from '../src/renderer/charts';

const bars: Plot = {
  panel: 'B', kind: 'bar', status: 'read', reason: null,
  y: { label: 'Maximum Load (N)', unit: 'N', scale: 'linear' }, x: { label: null, unit: null, scale: null },
  categories: ['Dry', 'Wet'],
  series: [
    { name: 'COL/PLA', colour: '#000000', values: [{ category: 'Dry', x: null, y: 354.16, err_lo: 36.9, err_hi: 36.1 }, { category: 'Wet', x: null, y: 267.44, err_lo: null, err_hi: 13.2 }] },
    { name: null, colour: '#595959', values: [{ category: 'Dry', x: null, y: 314.84, err_lo: 40, err_hi: 57 }] },
  ],
};

describe('the numbers read from a figure', () => {
  it('prints a value as a figure would, with its error bar', () => {
    expect([fmt(354.16), fmt(0.71086), fmt(52087), fmt(0), fmt(null)]).toEqual(['354', '0.711', '52087', '0', '']);
    expect(withError(bars.series[0]!.values[0]!)).toBe('354 ± 36.5');
    expect(withError(bars.series[0]!.values[1]!)).toBe('267 ± 13.2'); // the lower whisker hidden in the bar
    expect(withError(bars.series[1]!.values[0]!)).toBe('315 +57 −40');
  });

  it('lays a bar chart out a row per category, a column per series', () => {
    expect(plotTable(bars)).toEqual({ head: ['', 'COL/PLA', 'series 2'], rows: [['Dry', '354 ± 36.5', '315 +57 −40'], ['Wet', '267 ± 13.2', '']] });
    expect(plotTitle(bars)).toBe('(B) Maximum Load (N)');
  });

  it('lays points out a row per x, joining the same x read a hair apart', () => {
    const pts: Plot = { ...bars, kind: 'point', panel: null, x: { label: 'Days of Culture', unit: null, scale: 'linear' }, categories: [],
      series: [{ name: 'PLA', colour: null, values: [{ category: null, x: 0.97, y: 0.64, err_lo: null, err_hi: null }, { category: null, x: 27.94, y: 42.5, err_lo: 3.4, err_hi: 3.4 }] },
               { name: 'COL/PLA', colour: null, values: [{ category: null, x: 27.937, y: 50.3, err_lo: 3.3, err_hi: 3.25 }] }] };
    expect(plotTable(pts)).toEqual({ head: ['Days of Culture', 'PLA', 'COL/PLA'], rows: [['0.97', '0.64', ''], ['27.9', '42.5 ± 3.4', '50.3 ± 3.27']] });
  });

  it('copies a plot as the same CSV the worker writes', () => {
    expect(csvOf(bars).split('\n')).toEqual(['series,category,x,y,err_lo,err_hi', 'COL/PLA,Dry,,354.16,36.9,36.1', 'COL/PLA,Wet,,267.44,,13.2', 'series 2,Dry,,314.84,40,57', '']);
  });
});
