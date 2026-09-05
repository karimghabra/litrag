/**
 * The glyph repairs applied to PDF page text: only the unambiguous ones,
 * and never at the cost of text that was right to begin with.
 */

import { describe, expect, it } from 'vitest';
import { unmangle } from '../src/pdf.ts';

describe('unmangle', () => {
  it('restores µm and °C after a number', () => {
    expect(unmangle('threads were 50–100 m m in diameter')).toBe('threads were 50–100 µm in diameter');
    expect(unmangle('a grid (25 · 25 m m) was overlaid')).toBe('a grid (25 · 25 µm) was overlaid');
    expect(unmangle('incubated at 37 1 C overnight')).toBe('incubated at 37 °C overnight');
    expect(unmangle('dialyzed at 12–14 1 C.')).toBe('dialyzed at 12–14 °C.');
  });

  it('leaves honest text alone', () => {
    expect(unmangle('sheets of 30 × 10 mm dimensions')).toBe('sheets of 30 × 10 mm dimensions');
    expect(unmangle('per 1 cm width')).toBe('per 1 cm width');
    expect(unmangle('10 m masts')).toBe('10 m masts');
    expect(unmangle('0.1 m mol of reagent')).toBe('0.1 m mol of reagent');
    expect(unmangle('Figure 1 Cell morphology')).toBe('Figure 1 Cell morphology');
  });
});
