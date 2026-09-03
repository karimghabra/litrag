/**
 * Ollama: the local model server, for the machine with the GPU.
 *
 * Two things come from it. Embeddings, as an alternative to the CPU model
 * when a bigger one is worth running. And the rows the miner cannot mine —
 * claims, materials, methods, and parameters with the thing they measure
 * named — asked for as JSON against a schema, so the answer is rows or it is
 * nothing. Paper text goes to 127.0.0.1 and nowhere else.
 */

import type { Embedder } from './embed.ts';

export type FetchLike = (url: string, init?: { method?: string; headers?: Record<string, string>; body?: string }) => Promise<{ ok: boolean; status: number; text(): Promise<string> }>;

export interface OllamaSettings {
  url: string;
  /** The chat model that extracts rows. */
  chat: string;
  /** The embedding model, when embeddings come from Ollama. */
  embed: string;
}

export const OLLAMA_DEFAULTS: OllamaSettings = {
  url: 'http://127.0.0.1:11434',
  chat: 'qwen3:14b',
  embed: 'nomic-embed-text',
};

const defaultFetch: FetchLike = (url, init) => fetch(url, init);

async function post<T>(settings: OllamaSettings, path: string, body: unknown, fetchImpl: FetchLike): Promise<T> {
  const res = await fetchImpl(`${settings.url.replace(/\/$/, '')}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`Ollama ${path} answered ${res.status}: ${text.slice(0, 200)}`);
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error(`Ollama ${path} answered something that is not JSON: ${text.slice(0, 80)}`);
  }
}

export interface OllamaHealth {
  reachable: boolean;
  version?: string;
  models: string[];
  missing: string[];
}

/** Is the server there, and does it hold the models the library asks for? */
export async function ollamaHealth(settings: OllamaSettings, wanted: string[], fetchImpl: FetchLike = defaultFetch): Promise<OllamaHealth> {
  try {
    const base = settings.url.replace(/\/$/, '');
    const version = JSON.parse(await (await fetchImpl(`${base}/api/version`)).text()) as { version?: string };
    const tags = JSON.parse(await (await fetchImpl(`${base}/api/tags`)).text()) as { models?: { name: string }[] };
    const models = (tags.models ?? []).map((m) => m.name);
    const has = (name: string) => models.some((m) => m === name || m === `${name}:latest` || m.split(':')[0] === name.split(':')[0] && name.includes(':') === false);
    return { reachable: true, version: version.version, models, missing: wanted.filter((w) => !has(w)) };
  } catch {
    return { reachable: false, models: [], missing: wanted };
  }
}

export function ollamaEmbedder(settings: OllamaSettings, fetchImpl: FetchLike = defaultFetch): Embedder {
  let dims = 0;
  const embedder: Embedder = {
    model: `ollama:${settings.embed}`,
    get dims() {
      return dims;
    },
    async embedQuery(text) {
      return (await embedder.embed([text]))[0]!;
    },
    async embed(texts) {
      const out: Float32Array[] = [];
      for (let i = 0; i < texts.length; i += 32) {
        const batch = texts.slice(i, i + 32);
        const res = await post<{ embeddings?: number[][] }>(settings, '/api/embed', { model: settings.embed, input: batch }, fetchImpl);
        const rows = res.embeddings ?? [];
        if (rows.length !== batch.length) throw new Error(`Ollama embedded ${rows.length} of ${batch.length} texts.`);
        for (const row of rows) {
          dims = row.length;
          out.push(Float32Array.from(row));
        }
      }
      return out;
    },
  };
  return embedder;
}

/** What the model is asked to find in one stretch of a paper. */
export interface ExtractedRows {
  claims: { text: string; kind: 'finding' | 'method' | 'limitation' | 'hypothesis' | 'background' }[];
  materials: { name: string; role: string; amount?: string }[];
  methods: { name: string; description: string }[];
  parameters: { entity: string; value: string; unit: string; context: string }[];
}

export const ROWS_SCHEMA = {
  type: 'object',
  properties: {
    claims: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          text: { type: 'string' },
          kind: { type: 'string', enum: ['finding', 'method', 'limitation', 'hypothesis', 'background'] },
        },
        required: ['text', 'kind'],
      },
    },
    materials: {
      type: 'array',
      items: {
        type: 'object',
        properties: { name: { type: 'string' }, role: { type: 'string' }, amount: { type: 'string' } },
        required: ['name', 'role'],
      },
    },
    methods: {
      type: 'array',
      items: { type: 'object', properties: { name: { type: 'string' }, description: { type: 'string' } }, required: ['name', 'description'] },
    },
    parameters: {
      type: 'array',
      items: {
        type: 'object',
        properties: { entity: { type: 'string' }, value: { type: 'string' }, unit: { type: 'string' }, context: { type: 'string' } },
        required: ['entity', 'value', 'unit', 'context'],
      },
    },
  },
  required: ['claims', 'materials', 'methods', 'parameters'],
} as const;

const SYSTEM = `You read one section of a scientific paper about biomaterials and tissue engineering and return rows, as JSON matching the schema, and nothing else.

- claims: statements the section asserts as its own findings, method claims, limitations, hypotheses, or background it relies on. One sentence each, in the paper's own terms. Only what the text says.
- materials: every material, reagent, cell type or device named, with its role (scaffold material, crosslinker, solvent, cell source, buffer, equipment, ...) and the amount or concentration if given.
- methods: each procedure the section describes, named as the paper names it, with a one-sentence description.
- parameters: every measured or set quantity — what it is (entity, e.g. "EDC concentration", "tensile modulus", "crosslinking time"), the value, the unit, and the phrase it comes from.

Return empty arrays for what the section does not contain. Do not invent.`;

export interface Extractor {
  model: string;
  extract(section: { heading: string; text: string }, paper: { title: string }): Promise<ExtractedRows>;
}

export function ollamaExtractor(settings: OllamaSettings, fetchImpl: FetchLike = defaultFetch): Extractor {
  return {
    model: `ollama:${settings.chat}`,
    async extract(section, paper) {
      const res = await post<{ message?: { content?: string } }>(
        settings,
        '/api/chat',
        {
          model: settings.chat,
          stream: false,
          think: false,
          format: ROWS_SCHEMA,
          options: { temperature: 0, num_ctx: 8192 },
          messages: [
            { role: 'system', content: SYSTEM },
            { role: 'user', content: `Paper: ${paper.title}\nSection: ${section.heading}\n\n${section.text}` },
          ],
        },
        fetchImpl,
      );
      return parseRows(res.message?.content ?? '');
    },
  };
}

/** The model's answer as rows, with anything malformed dropped rather than trusted. */
export function parseRows(content: string): ExtractedRows {
  let raw: unknown;
  try {
    raw = JSON.parse(content);
  } catch {
    throw new Error('The model did not answer with JSON.');
  }
  const obj = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const arr = (key: string) => (Array.isArray(obj[key]) ? (obj[key] as unknown[]) : []);
  const str = (v: unknown) => (typeof v === 'string' ? v.trim() : '');
  const kinds = new Set(['finding', 'method', 'limitation', 'hypothesis', 'background']);
  return {
    claims: arr('claims')
      .map((c) => c as Record<string, unknown>)
      .filter((c) => str(c['text']))
      .map((c) => ({ text: str(c['text']), kind: (kinds.has(str(c['kind'])) ? str(c['kind']) : 'finding') as ExtractedRows['claims'][number]['kind'] })),
    materials: arr('materials')
      .map((m) => m as Record<string, unknown>)
      .filter((m) => str(m['name']))
      .map((m) => ({ name: str(m['name']), role: str(m['role']) || 'unspecified', amount: str(m['amount']) || undefined })),
    methods: arr('methods')
      .map((m) => m as Record<string, unknown>)
      .filter((m) => str(m['name']))
      .map((m) => ({ name: str(m['name']), description: str(m['description']) })),
    parameters: arr('parameters')
      .map((p) => p as Record<string, unknown>)
      .filter((p) => str(p['entity']) && str(p['value']))
      .map((p) => ({ entity: str(p['entity']), value: str(p['value']), unit: str(p['unit']), context: str(p['context']) })),
  };
}
