/**
 * The local miner: every number with a unit, and the sentence it sits in.
 *
 * No model. "What crosslinker concentrations has anyone used" is a question
 * about rows, and the rows are here: a value, a unit, a kind, the sentence.
 * The kinds are the ones a tissue-engineering methods section is made of.
 */

import type { ParameterInput, SectionInput } from './db.ts';
import { splitSentences } from './chunk.ts';

interface UnitSpec {
  re: string;
  unit: string;
  kind: string;
}

const UNITS: UnitSpec[] = [
  { re: 'mg\\s*/\\s*m[lL]', unit: 'mg/mL', kind: 'concentration' },
  { re: '[µu]g\\s*/\\s*m[lL]', unit: 'µg/mL', kind: 'concentration' },
  { re: 'ng\\s*/\\s*m[lL]', unit: 'ng/mL', kind: 'concentration' },
  { re: 'g\\s*/\\s*[lL]', unit: 'g/L', kind: 'concentration' },
  { re: 'mg\\s*/\\s*g', unit: 'mg/g', kind: 'ratio' },
  { re: 'U\\s*/\\s*m[lL]', unit: 'U/mL', kind: 'concentration' },
  { re: 'cells\\s*/\\s*(?:m[lL]|cm2|cm²|well|scaffold)', unit: 'cells/unit', kind: 'density' },
  { re: 'mM', unit: 'mM', kind: 'concentration' },
  { re: '[µu]M', unit: 'µM', kind: 'concentration' },
  { re: 'nM', unit: 'nM', kind: 'concentration' },
  { re: 'M(?![a-zA-Z])', unit: 'M', kind: 'concentration' },
  { re: '%\\s*\\(?\\s*w\\s*/\\s*v\\s*\\)?', unit: '% w/v', kind: 'concentration' },
  { re: '%\\s*\\(?\\s*v\\s*/\\s*v\\s*\\)?', unit: '% v/v', kind: 'concentration' },
  { re: '%\\s*\\(?\\s*w\\s*/\\s*w\\s*\\)?', unit: '% w/w', kind: 'concentration' },
  { re: 'wt\\s*%|%\\s*wt', unit: 'wt%', kind: 'concentration' },
  { re: '%', unit: '%', kind: 'fraction' },
  { re: '°\\s*C|º\\s*C|˚\\s*C|degrees?\\s+(?:C|Celsius)', unit: '°C', kind: 'temperature' },
  { re: 'kV\\s*/\\s*cm|V\\s*/\\s*cm|V\\s*/\\s*mm', unit: 'V/cm', kind: 'field' },
  { re: 'kV', unit: 'kV', kind: 'voltage' },
  { re: 'mV', unit: 'mV', kind: 'voltage' },
  { re: 'V(?![a-zA-Z/])', unit: 'V', kind: 'voltage' },
  { re: 'mA\\s*/\\s*cm2|mA\\s*/\\s*cm²|A\\s*/\\s*m2|A\\s*/\\s*m²', unit: 'mA/cm²', kind: 'current density' },
  { re: '[µu]A', unit: 'µA', kind: 'current' },
  { re: 'mA', unit: 'mA', kind: 'current' },
  { re: 'GPa', unit: 'GPa', kind: 'stress' },
  { re: 'MPa', unit: 'MPa', kind: 'stress' },
  { re: 'kPa', unit: 'kPa', kind: 'stress' },
  { re: 'Pa(?![a-zA-Z])', unit: 'Pa', kind: 'stress' },
  { re: 'mN', unit: 'mN', kind: 'force' },
  { re: 'N(?![a-zA-Z])', unit: 'N', kind: 'force' },
  { re: 'nm', unit: 'nm', kind: 'length' },
  { re: '[µu]m', unit: 'µm', kind: 'length' },
  { re: 'mm\\s*/\\s*min', unit: 'mm/min', kind: 'rate' },
  { re: 'mm', unit: 'mm', kind: 'length' },
  { re: 'cm(?![a-zA-Z²2])', unit: 'cm', kind: 'length' },
  { re: 'k?Hz', unit: 'Hz', kind: 'frequency' },
  { re: 'rpm', unit: 'rpm', kind: 'rate' },
  { re: 'm[lL](?![a-zA-Z])', unit: 'mL', kind: 'volume' },
  { re: '[µu][lL](?![a-zA-Z])', unit: 'µL', kind: 'volume' },
  { re: 'mg(?![a-zA-Z/])', unit: 'mg', kind: 'mass' },
  { re: '[µu]g(?![a-zA-Z/])', unit: 'µg', kind: 'mass' },
  { re: 'kDa', unit: 'kDa', kind: 'mass' },
  { re: 'g(?![a-zA-Z/])', unit: 'g', kind: 'mass' },
  { re: 'weeks?|wk', unit: 'week', kind: 'time' },
  { re: 'days?(?![a-zA-Z])|d(?![a-zA-Z])', unit: 'day', kind: 'time' },
  { re: 'h(?:ours?|rs?)?(?![a-zA-Z])', unit: 'h', kind: 'time' },
  { re: 'min(?:utes?|s)?(?![a-zA-Z])', unit: 'min', kind: 'time' },
  { re: 's(?:ec(?:onds?)?)?(?![a-zA-Z])', unit: 's', kind: 'time' },
];

const NUMBER = '(?<value>[-−]?\\d+(?:[.,]\\d+)?(?:\\s*(?:[-–−]|to|±|\\+/-)\\s*\\d+(?:[.,]\\d+)?)?)';
const WITH_UNIT = new RegExp(`(?<![\\w.])${NUMBER}\\s*(?<unit>${UNITS.map((u) => `(?:${u.re})`).join('|')})`, 'g');
const PH = /\bpH\s*(?:of\s*|=\s*|~\s*)?(?<value>\d+(?:[.,]\d+)?(?:\s*(?:[-–]|to)\s*\d+(?:[.,]\d+)?)?)/g;

function specFor(unitText: string): UnitSpec | undefined {
  for (const spec of UNITS) if (new RegExp(`^(?:${spec.re})$`).test(unitText)) return spec;
  return undefined;
}

function numeric(value: string): number | null {
  const first = value.replace(/−/g, '-').match(/-?\d+(?:[.,]\d+)?/);
  if (!first) return null;
  const n = Number(first[0].replace(',', '.'));
  return Number.isFinite(n) ? n : null;
}

/** Parameters in one text, sentence by sentence. */
export function mineParameters(text: string): Omit<ParameterInput, 'sectionOrdinal'>[] {
  const out: Omit<ParameterInput, 'sectionOrdinal'>[] = [];
  for (const sentence of splitSentences(text)) {
    for (const m of sentence.matchAll(WITH_UNIT)) {
      const value = m.groups?.['value'] ?? '';
      const unitText = m.groups?.['unit'] ?? '';
      const spec = specFor(unitText);
      if (!spec) continue;
      // A bare year ("in 2019") or a citation number is not a measurement.
      if (spec.unit === 's' && /^\d{4}$/.test(value)) continue;
      out.push({ value: value.replace(/\s+/g, ' ').trim(), valueNum: numeric(value), unit: spec.unit, kind: spec.kind, sentence });
    }
    for (const m of sentence.matchAll(PH)) {
      const value = m.groups?.['value'] ?? '';
      out.push({ value, valueNum: numeric(value), unit: 'pH', kind: 'ph', sentence });
    }
  }
  return out;
}

/** The miner over every section of a paper that is its own prose. */
export function mineSections(sections: SectionInput[]): ParameterInput[] {
  const out: ParameterInput[] = [];
  sections.forEach((section, sectionOrdinal) => {
    if (section.kind === 'references' || section.kind === 'back') return;
    for (const p of mineParameters(section.text)) out.push({ sectionOrdinal, ...p });
  });
  return out;
}
