/**
 * Embeddings: the local model, and a stand-in for tests.
 *
 * `bge-small-en-v1.5` quantised runs on the CPU in a few milliseconds a
 * sentence and downloads once, into the library root. The stand-in hashes
 * words into a small vector, so a test can check that retrieval finds the
 * chunk about crosslinking without a 34 MB download.
 */

export interface Embedder {
  model: string;
  dims: number;
  embed(texts: string[]): Promise<Float32Array[]>;
  /** A question, embedded the way the model wants questions: BGE v1.5 asks for an instruction prefix. */
  embedQuery(text: string): Promise<Float32Array>;
}

/** The instruction BGE v1.5 models were trained to see in front of a short query. */
export const BGE_QUERY_PREFIX = 'Represent this sentence for searching relevant passages: ';

export function cosine(a: Float32Array, b: Float32Array): number {
  let dot = 0;
  let na = 0;
  let nb = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    dot += a[i]! * b[i]!;
    na += a[i]! * a[i]!;
    nb += b[i]! * b[i]!;
  }
  return na && nb ? dot / Math.sqrt(na * nb) : 0;
}

export async function localEmbedder(cacheDir: string, model: string): Promise<Embedder> {
  const transformers = await import('@huggingface/transformers');
  transformers.env.cacheDir = cacheDir;
  const extractor = await transformers.pipeline('feature-extraction', model, { dtype: 'q8' });
  let dims = 0;
  const bge = /bge-.*-v1\.5/i.test(model);
  const embedder: Embedder = {
    model,
    get dims() {
      return dims;
    },
    async embedQuery(text) {
      const [v] = await embedder.embed([bge ? `${BGE_QUERY_PREFIX}${text}` : text]);
      return v!;
    },
    async embed(texts) {
      const out: Float32Array[] = [];
      for (let i = 0; i < texts.length; i += 16) {
        const batch = texts.slice(i, i + 16);
        const tensor = await extractor(batch, { pooling: 'cls', normalize: true });
        const rows = tensor.tolist() as number[][];
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

/** Deterministic, model-free: a bag of hashed words, unit length. */
export function hashEmbedder(dims = 64): Embedder {
  const embedder: Embedder = {
    model: `hash-${dims}`,
    dims,
    async embedQuery(text) {
      return (await embedder.embed([text]))[0]!;
    },
    async embed(texts) {
      return texts.map((text) => {
        const v = new Float32Array(dims);
        for (const word of text.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length > 2)) {
          let h = 2166136261;
          for (let i = 0; i < word.length; i++) h = Math.imul(h ^ word.charCodeAt(i), 16777619) >>> 0;
          v[h % dims]! += 1;
        }
        let norm = 0;
        for (const x of v) norm += x * x;
        norm = Math.sqrt(norm) || 1;
        for (let i = 0; i < dims; i++) v[i]! /= norm;
        return v;
      });
    },
  };
  return embedder;
}
