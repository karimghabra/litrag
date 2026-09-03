/**
 * The one place `lit` touches Protracker: asking `pt` what a project ref is.
 *
 * `pt --json show <ref>` answers with the node's id, kind, name and ref. When
 * `pt` is not on the PATH the library stands alone, and `init` says so.
 */

import { spawnSync } from 'node:child_process';

export type ProjectLookup =
  | { kind: 'project'; id: string; name: string; ref: string }
  | { kind: 'other'; name: string; nodeKind: string }
  | { kind: 'none' }
  | { kind: 'no-pt' };

export function resolveProject(token: string, vault?: string, command = process.env['LITRAG_PT'] ?? 'pt'): ProjectLookup {
  // LITRAG_PT may carry a command with arguments ("node --experimental-transform-types …/bin.ts").
  const [exe, ...pre] = command.trim().split(/\s+/);
  const args = [...pre, ...(vault ? ['--vault', vault] : []), '--json', 'show', token];
  const result = spawnSync(exe!, args, { encoding: 'utf8', shell: process.platform === 'win32' });
  if (result.error) return { kind: 'no-pt' };
  if (result.status !== 0) return { kind: 'none' };
  try {
    const node = JSON.parse(result.stdout) as { id?: string; kind?: string; name?: string; ref?: string };
    if (!node.id || !node.name) return { kind: 'none' };
    if (node.kind !== 'project') return { kind: 'other', name: node.name, nodeKind: node.kind ?? 'node' };
    return { kind: 'project', id: node.id, name: node.name, ref: node.ref ?? node.id };
  } catch {
    return { kind: 'none' };
  }
}
