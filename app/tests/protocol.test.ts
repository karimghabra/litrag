import { describe, expect, it } from 'vitest';
import { closesRequest, LineSplitter, parseEvent } from '../src/main/protocol.ts';

describe('LineSplitter', () => {
  it('holds a partial line until the rest arrives', () => {
    const s = new LineSplitter();
    expect(s.push('{"event":"a"}\n{"event":')).toEqual(['{"event":"a"}']);
    expect(s.push('"b"}\n')).toEqual(['{"event":"b"}']);
    expect(s.flush()).toEqual([]);
  });
  it('flushes a trailing line without a newline', () => {
    const s = new LineSplitter();
    expect(s.push('{"event":"x"}')).toEqual([]);
    expect(s.flush()).toEqual(['{"event":"x"}']);
  });
});

describe('parseEvent', () => {
  it('accepts an object with an event name and nothing else', () => {
    expect(parseEvent('{"event":"stage","id":"r1"}')).toEqual({ event: 'stage', id: 'r1' });
    expect(parseEvent('Loading weights 100%')).toBeNull();
    expect(parseEvent('{"id":"r1"}')).toBeNull();
  });
});

describe('closesRequest', () => {
  it('closes a read on its answer and an ingest on done, not on a streamed tree', () => {
    expect(closesRequest({ event: 'tree', id: 'r1' }, 'tree')).toBe(true);
    expect(closesRequest({ event: 'tree', id: 'r2' }, 'ingest')).toBe(false);
    expect(closesRequest({ event: 'stage', id: 'r2' }, 'ingest')).toBe(false);
    expect(closesRequest({ event: 'done', id: 'r2' }, 'ingest')).toBe(true);
    expect(closesRequest({ event: 'queued', id: 'r2' }, 'ingest')).toBe(true);
    expect(closesRequest({ event: 'edges', id: 'r3' }, 'edges')).toBe(true);
    expect(closesRequest({ event: 'error', id: 'r3' }, 'papers')).toBe(true);
  });
});
