/**
 * A library: one project's papers, beside the notebook and never inside it.
 *
 * The root holds one folder per library and the shared model cache. A
 * library is a manifest, a SQLite store, the papers the tool fetched, and an
 * inbox for the ones a person brings. The manifest carries the vault project
 * id, so a rename in the tracker does not orphan the papers.
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export const DEFAULT_MODEL = 'Xenova/bge-small-en-v1.5';

export interface Manifest {
  /** The folder name: a slug of the name at creation. */
  id: string;
  name: string;
  /** The vault node id of the project this library serves, when it serves one. */
  projectId?: string;
  projectRef?: string;
  /** Libraries whose papers this one also searches — the shared spine. */
  includes: string[];
  /** Saved searches, run again by `refresh`. */
  queries: string[];
  /** How rows are extracted beyond the miner: not at all, a local model through Ollama, or Claude. */
  extract: 'local' | 'ollama' | 'claude';
  /** Where embeddings come from: the CPU model in-process, or Ollama on the GPU. */
  embedding: 'transformers' | 'ollama';
  /** The embedding model every vector in the store was made with. */
  model: string;
  /** The local model server, when either stage uses it. */
  ollama: { url: string; chat: string; embed: string };
  createdAt: string;
}

export const OLLAMA_DEFAULTS = { url: 'http://127.0.0.1:11434', chat: 'qwen3:14b', embed: 'nomic-embed-text' };

export interface Library {
  root: string;
  dir: string;
  manifestPath: string;
  dbPath: string;
  papersDir: string;
  inboxDir: string;
  manifest: Manifest;
}

/** Where the libraries live: LITRAG_ROOT, else the tracker's setting, else the tracker's default — so `lit` and Protracker agree on one place. */
export function libraryRoot(env: NodeJS.ProcessEnv = process.env): string {
  return env['LITRAG_ROOT'] || env['PROTRACKER_LIBRARY'] || join(homedir(), '.protracker', 'library');
}

export function modelCacheDir(root: string): string {
  return join(root, 'models');
}

export function slugify(name: string): string {
  return name
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'library';
}

function pathsFor(root: string, id: string, manifest: Manifest): Library {
  const dir = join(root, id);
  return {
    root,
    dir,
    manifestPath: join(dir, 'library.json'),
    dbPath: join(dir, 'lit.sqlite'),
    papersDir: join(dir, 'papers'),
    inboxDir: join(dir, 'inbox'),
    manifest,
  };
}

function readManifest(path: string): Manifest | null {
  if (!existsSync(path)) return null;
  const raw = JSON.parse(readFileSync(path, 'utf8')) as Partial<Manifest>;
  if (!raw.id || !raw.name) return null;
  return {
    id: raw.id,
    name: raw.name,
    projectId: raw.projectId,
    projectRef: raw.projectRef,
    includes: raw.includes ?? [],
    queries: raw.queries ?? [],
    extract: raw.extract === 'claude' || raw.extract === 'ollama' ? raw.extract : 'local',
    embedding: raw.embedding === 'ollama' ? 'ollama' : 'transformers',
    model: raw.model ?? DEFAULT_MODEL,
    ollama: { ...OLLAMA_DEFAULTS, ...(raw.ollama ?? {}) },
    createdAt: raw.createdAt ?? '',
  };
}

/** Written with sorted keys so two writes of one manifest are one byte string. */
export function saveManifest(lib: Library): void {
  mkdirSync(lib.dir, { recursive: true });
  const m = lib.manifest;
  const ordered: Record<string, unknown> = {};
  for (const key of Object.keys(m).sort()) {
    const value = (m as unknown as Record<string, unknown>)[key];
    if (value !== undefined) ordered[key] = value;
  }
  writeFileSync(lib.manifestPath, `${JSON.stringify(ordered, null, 2)}\n`);
}

export function listLibraries(root: string): Library[] {
  if (!existsSync(root)) return [];
  const out: Library[] = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const manifest = readManifest(join(root, entry.name, 'library.json'));
    if (manifest) out.push(pathsFor(root, entry.name, manifest));
  }
  return out.sort((a, b) => a.manifest.name.localeCompare(b.manifest.name));
}

/** By folder id, by vault project id, or by name — the same rule as a vault ref. */
export function openLibrary(root: string, key: string): Library | null {
  const wanted = key.trim().toLowerCase();
  for (const lib of listLibraries(root)) {
    const m = lib.manifest;
    if (m.id === wanted || m.projectId?.toLowerCase() === wanted || m.name.toLowerCase() === wanted) return lib;
  }
  return null;
}

export interface LibraryInit {
  name: string;
  projectId?: string;
  projectRef?: string;
  includes?: string[];
  queries?: string[];
  model?: string;
  extract?: Manifest['extract'];
  embedding?: Manifest['embedding'];
  ollama?: Partial<Manifest['ollama']>;
  now?: string;
}

export function createLibrary(root: string, init: LibraryInit): Library {
  const id = slugify(init.name);
  const clash = listLibraries(root).find(
    (l) => l.manifest.id === id || (init.projectId !== undefined && l.manifest.projectId === init.projectId),
  );
  if (clash) throw new Error(`A library for that already exists: ${clash.manifest.id} (${clash.manifest.name}).`);
  const manifest: Manifest = {
    id,
    name: init.name.trim(),
    projectId: init.projectId,
    projectRef: init.projectRef,
    includes: init.includes ?? [],
    queries: init.queries ?? [],
    extract: init.extract ?? 'local',
    embedding: init.embedding ?? 'transformers',
    model: init.model ?? (init.embedding === 'ollama' ? `ollama:${init.ollama?.embed ?? OLLAMA_DEFAULTS.embed}` : DEFAULT_MODEL),
    ollama: { ...OLLAMA_DEFAULTS, ...(init.ollama ?? {}) },
    createdAt: init.now ?? new Date().toISOString().slice(0, 16),
  };
  const lib = pathsFor(root, id, manifest);
  mkdirSync(lib.papersDir, { recursive: true });
  mkdirSync(lib.inboxDir, { recursive: true });
  saveManifest(lib);
  return lib;
}
