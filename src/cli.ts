/**
 * `lit` — the literature loop's command line.
 *
 * Every verb takes `--json`; the human form is for people. A library can be
 * tied to a Protracker project: `init` asks `pt` for the ref when `pt` is on
 * the PATH, and takes `--project-id`/`--project-ref` when it is not.
 */

import { list, one, parseArgs, type Args } from './args.ts';
import { resolveProject } from './protracker.ts';
import { allPapers, openDb, statusView, upsertPaper } from './db.ts';
import { hashEmbedder, localEmbedder, type Embedder } from './embed.ts';
import { annotateLibrary, extractLibrary, fetchCandidates, ingestLibrary } from './ingest.ts';
import { buildGraph, graphStats } from './graph.ts';
import { createLibrary, libraryRoot, listLibraries, modelCacheDir, openLibrary, saveManifest, type Library, type Manifest } from './library.ts';
import { ollamaEmbedder, ollamaExtractor, ollamaHealth } from './ollama.ts';
import { queryLibrary, runSql } from './query.ts';
import { lookupEuropePmc, referencesOf, searchEuropePmc } from './sources/europepmc.ts';
import { copyFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { basename, join } from 'node:path';

const HELP = `lit — the literature loop: one library of papers per project

usage: lit [--root DIR] [--json] <command> [args]

  libraries                             every library, with its project and counts
  init <project-ref | name> [--query Q]... [--include LIB]... [--no-project]
                                        make a library; a Protracker ref ties it to its project
       [--project-id ID --project-ref REF]   tie it by hand when pt is not on the PATH
       [--extract local|ollama] [--embed transformers|ollama]
       [--ollama-url URL] [--ollama-chat MODEL] [--ollama-embed MODEL]
  config <lib> [the same flags]         change how a library extracts and embeds
  doctor [<lib>]                        is everything in place: root, model cache, Ollama and its models
  search <lib> <query> [--since YEAR] [--limit N]
                                        stage candidates from Europe PMC; nothing is fetched
  add <lib> <doi | pmid | pmcid | file.pdf>
                                        one paper by hand; a PDF goes to the inbox
  fetch <lib>                           full text for every candidate that has any
  ingest <lib> [--reread]               read fetched papers and the inbox into rows; embed.
                                        --reread reads every paper on disk again
  extract <lib> [--limit N]             the model stage: claims, materials, methods and named
                                        parameters, through Ollama (needs --extract ollama)
  annotate <lib>                        entity nodes for free: Europe PMC's text-mined terms
                                        (chemicals, proteins, organisms, methods) per paper
  graph <lib>                           the graph as it stands: nodes, edges, the entities that span most papers
  entities <lib> [--kind K] [--limit N] the entities, with how many papers name each
  refresh <lib>                         the saved searches, then fetch, ingest, and extract
  status <lib>                          counts by stage, and what needs a PDF
  wanted <lib> [--csv FILE]             the PDFs to collect, most-cited first, with doi.org links
  papers <lib>                          the papers, one line each
  query <lib> <question> [--limit N] [--no-spine] [--no-graph] [--trace]
                                        chunks with citations: words, meaning and the graph walk fused
  sql <lib> <select ...> [--limit N]    read-only SQL against the store
  snowball <lib> <paper-key>            stage what a paper cites
  where                                 the library root

  --root DIR   defaults to $LITRAG_ROOT, else $PROTRACKER_LIBRARY, else ~/.protracker/library
  --vault DIR  the Protracker vault to resolve a project ref in (init only); defaults as pt does

A <lib> is a library id (looped-ligament), its project's vault id (n156), or its name.
`;

function need(lib: Library | null, key: string | undefined): Library {
  if (!key) throw new Error('Which library? Give its id, its project id, or its name; `lit libraries` lists them.');
  if (!lib) throw new Error(`No library matching "${key}". \`lit libraries\` lists them; \`lit init\` makes one.`);
  return lib;
}

async function embedderFor(root: string, lib: Library, flags: Args['flags']): Promise<Embedder> {
  if (flags['fake-embedder'] === true) return hashEmbedder();
  if (lib.manifest.embedding === 'ollama') return ollamaEmbedder(lib.manifest.ollama);
  return localEmbedder(modelCacheDir(root), lib.manifest.model);
}

/** The backend flags, read the same way by init and config. */
function backendFlags(flags: Args['flags']): { extract?: Manifest['extract']; embedding?: Manifest['embedding']; ollama: Partial<Manifest['ollama']> } {
  const extract = one(flags['extract']);
  const embedding = one(flags['embed']);
  if (extract !== undefined && extract !== 'local' && extract !== 'ollama') throw new Error('--extract is local or ollama.');
  if (embedding !== undefined && embedding !== 'transformers' && embedding !== 'ollama') throw new Error('--embed is transformers or ollama.');
  const ollama: Partial<Manifest['ollama']> = {};
  const url = one(flags['ollama-url']);
  const chat = one(flags['ollama-chat']);
  const embed = one(flags['ollama-embed']);
  if (url) ollama.url = url;
  if (chat) ollama.chat = chat;
  if (embed) ollama.embed = embed;
  return { extract: extract as Manifest['extract'] | undefined, embedding: embedding as Manifest['embedding'] | undefined, ollama };
}

function extractorFor(lib: Library) {
  if (lib.manifest.extract !== 'ollama') {
    throw new Error(`Library "${lib.manifest.id}" extracts with the local miner only. \`lit config ${lib.manifest.id} --extract ollama\` turns the model stage on.`);
  }
  return ollamaExtractor(lib.manifest.ollama);
}

async function main(argv: string[]): Promise<number> {
  const { positional, flags } = parseArgs(argv);
  const json = flags['json'] === true;
  if (positional.length === 0 || flags['help'] || positional[0] === 'help') {
    process.stdout.write(HELP);
    return 0;
  }
  const root = one(flags['root']) ?? libraryRoot();
  const out = (value: unknown) => process.stdout.write(`${typeof value === 'string' ? value : JSON.stringify(value, null, 2)}\n`);
  const log = (line: string) => {
    if (!json) process.stderr.write(`${line}\n`);
  };
  const [command, ...rest] = positional;
  const now = new Date().toISOString().slice(0, 16);

  try {
    switch (command) {
      case 'where':
        return out(json ? { root } : root), 0;

      case 'libraries': {
        const libs = listLibraries(root).map((lib) => {
          const db = openDb(lib.dbPath);
          try {
            const status = statusView(db, lib.manifest.model);
            return { id: lib.manifest.id, name: lib.manifest.name, projectId: lib.manifest.projectId ?? null, projectRef: lib.manifest.projectRef ?? null, includes: lib.manifest.includes, queries: lib.manifest.queries, papers: status.papers, chunks: status.chunks };
          } finally {
            db.close();
          }
        });
        if (json) return out(libs), 0;
        if (!libs.length) return out(`No libraries under ${root}. \`lit init <project>\` makes one.`), 0;
        for (const l of libs) {
          const p = l.papers;
          out(`${l.id.padEnd(24)} ${l.name}  ${l.projectId ?? '—'}  ${p.ingested} ingested · ${p.fetched} fetched · ${p.candidate} candidates · ${p['needs-pdf']} need a PDF`);
        }
        return 0;
      }

      case 'init': {
        const token = rest.join(' ').trim();
        if (!token) throw new Error('Which project? lit init <project-ref | name>');
        let name = token;
        let projectId = one(flags['project-id']);
        let projectRef = one(flags['project-ref']);
        if (flags['no-project'] !== true && projectId === undefined) {
          const found = resolveProject(token, one(flags['vault']));
          if (found.kind === 'project') {
            name = found.name;
            projectId = found.id;
            projectRef = found.ref;
          } else if (found.kind === 'other') {
            throw new Error(`"${found.name}" is a ${found.nodeKind}; a library belongs to a project. Pass --no-project for a library of its own.`);
          } else if (found.kind === 'no-pt') {
            log('Note: `pt` is not on the PATH, so the library is not tied to a Protracker project. Pass --project-id and --project-ref to tie it by hand, or --no-project to say so.');
          } else {
            log(`Note: no project in the vault matches "${token}"; making a library of its own. Pass --no-project to say so.`);
          }
        }
        const backend = backendFlags(flags);
        const lib = createLibrary(root, { name, projectId, projectRef, includes: list(flags['include']), queries: list(flags['query']), now, ...backend });
        openDb(lib.dbPath).close();
        const delta = { ok: true as const, id: lib.manifest.id, dir: lib.dir, projectId: projectId ?? null, message: `Made library "${lib.manifest.name}" (${lib.manifest.id})${projectId ? ` for project ${projectId}` : ''} at ${lib.dir}.` };
        return out(json ? delta : delta.message), 0;
      }

      case 'config': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const backend = backendFlags(flags);
        if (backend.extract) lib.manifest.extract = backend.extract;
        if (backend.embedding) {
          lib.manifest.embedding = backend.embedding;
        }
        lib.manifest.ollama = { ...lib.manifest.ollama, ...backend.ollama };
        // The vector store is keyed by model, so a change here means the next
        // ingest embeds everything again for the new model; nothing is lost.
        if (backend.embedding || backend.ollama.embed) {
          lib.manifest.model = lib.manifest.embedding === 'ollama' ? `ollama:${lib.manifest.ollama.embed}` : (one(flags['model']) ?? lib.manifest.model.replace(/^ollama:.*/, 'Xenova/bge-small-en-v1.5'));
        }
        for (const inc of list(flags['include'])) if (!lib.manifest.includes.includes(inc)) lib.manifest.includes.push(inc);
        saveManifest(lib);
        if (json) return out(lib.manifest), 0;
        out(`${lib.manifest.id}: extract ${lib.manifest.extract}, embed ${lib.manifest.embedding} (${lib.manifest.model})${lib.manifest.extract === 'ollama' || lib.manifest.embedding === 'ollama' ? `, Ollama at ${lib.manifest.ollama.url} — chat ${lib.manifest.ollama.chat}, embed ${lib.manifest.ollama.embed}` : ''}.`);
        if (backend.embedding || backend.ollama.embed) out(`The next \`lit ingest ${lib.manifest.id}\` embeds every chunk for ${lib.manifest.model}.`);
        return 0;
      }

      case 'doctor': {
        const lib = rest[0] ? need(openLibrary(root, rest[0]), rest[0]) : null;
        const report: Record<string, unknown> = { root, rootExists: existsSync(root), modelCache: existsSync(modelCacheDir(root)) };
        if (lib) {
          report['library'] = lib.manifest.id;
          const wanted = [...(lib.manifest.extract === 'ollama' ? [lib.manifest.ollama.chat] : []), ...(lib.manifest.embedding === 'ollama' ? [lib.manifest.ollama.embed] : [])];
          if (wanted.length) report['ollama'] = await ollamaHealth(lib.manifest.ollama, wanted);
        } else {
          report['ollama'] = await ollamaHealth({ url: 'http://127.0.0.1:11434', chat: '', embed: '' }, []);
        }
        if (json) return out(report), 0;
        out(`root        ${root}${report['rootExists'] ? '' : '  (not made yet)'}`);
        out(`model cache ${report['modelCache'] ? 'present' : 'empty — the first ingest downloads the CPU model'}`);
        const health = report['ollama'] as { reachable: boolean; version?: string; models: string[]; missing: string[] } | undefined;
        if (health) {
          if (!health.reachable) out(`ollama      not reachable${lib ? ` at ${lib.manifest.ollama.url}` : ''} — is it running?`);
          else {
            out(`ollama      ${health.version ?? 'reachable'}, ${health.models.length} model${health.models.length === 1 ? '' : 's'}: ${health.models.join(', ') || 'none'}`);
            if (health.missing.length) out(`            missing: ${health.missing.join(', ')} — \`ollama pull <model>\` gets them`);
          }
        }
        return 0;
      }

      case 'extract': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const extractor = extractorFor(lib);
        const health = await ollamaHealth(lib.manifest.ollama, [lib.manifest.ollama.chat]);
        if (!health.reachable) throw new Error(`Ollama is not reachable at ${lib.manifest.ollama.url}. Start it, then try again.`);
        if (health.missing.length) throw new Error(`Ollama has no model "${lib.manifest.ollama.chat}". \`ollama pull ${lib.manifest.ollama.chat}\` gets it.`);
        const limit = one(flags['limit']);
        const report = await extractLibrary(lib, extractor, { log, now, limit: limit ? Number(limit) : undefined });
        const delta = { ok: true as const, ...report, message: `Extracted ${report.extracted.length} paper${report.extracted.length === 1 ? '' : 's'} (${report.sections} sections)${report.failed.length ? `; ${report.failed.length} failed` : ''}.` };
        return out(json ? delta : delta.message), 0;
      }

      case 'annotate': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const report = await annotateLibrary(lib, { log, now });
        const delta = { ok: true as const, ...report, message: `Annotated ${report.annotated.length} paper${report.annotated.length === 1 ? '' : 's'} (${report.mentions} mentions)${report.skipped.length ? `; ${report.skipped.length} have no PMCID, so no terms` : ''}.` };
        return out(json ? delta : delta.message), 0;
      }

      case 'graph': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const db = openDb(lib.dbPath, { readOnly: true });
        try {
          const stats = graphStats(db, buildGraph(db));
          if (json) return out(stats), 0;
          out(`${stats.papers} papers · ${stats.chunks} chunks · ${stats.entities} entities (${stats.hubsDropped} hubs left out) · ${stats.citationEdges} citation edges · ${stats.mentionEdges} mention edges`);
          for (const e of stats.topEntities) out(`  ${String(e.papers).padStart(4)} papers  ${e.kind.padEnd(20)} ${e.name}`);
          return 0;
        } finally {
          db.close();
        }
      }

      case 'entities': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const kind = one(flags['kind']);
        const limit = Number(one(flags['limit']) ?? 50);
        const rows = runSql(lib, `select e.name, e.kind, count(distinct m.paper) as papers, sum(m.count) as mentions from entities e join mentions m on m.entity = e.id ${kind ? `where e.kind = '${kind.replace(/'/g, "''")}'` : ''} group by e.id order by papers desc, mentions desc`, limit).rows;
        if (json) return out(rows), 0;
        for (const r of rows) out(`${String(r['papers']).padStart(4)} papers  ${String(r['mentions']).padStart(5)} mentions  ${String(r['kind']).padEnd(20)} ${String(r['name'])}`);
        if (!rows.length) out('No entities yet. `lit annotate` or `lit extract` makes them.');
        return 0;
      }

      case 'search': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const query = rest.slice(1).join(' ').trim();
        if (!query) throw new Error('Search for what? lit search <lib> <query>');
        const since = one(flags['since']);
        const limit = one(flags['limit']);
        const hits = await searchEuropePmc(query, { since: since ? Number(since) : undefined, limit: limit ? Number(limit) : 25 });
        const db = openDb(lib.dbPath);
        let created = 0;
        try {
          for (const hit of hits) if (upsertPaper(db, hit, now).created) created += 1;
          db.prepare("INSERT INTO queries (source, query, last_run, hits) VALUES ('europepmc', ?, ?, ?) ON CONFLICT(source, query) DO UPDATE SET last_run = excluded.last_run, hits = excluded.hits").run(query, now, hits.length);
        } finally {
          db.close();
        }
        if (!lib.manifest.queries.includes(query)) {
          lib.manifest.queries.push(query);
          saveManifest(lib);
        }
        const delta = { ok: true as const, hits: hits.length, staged: created, message: `${hits.length} hit${hits.length === 1 ? '' : 's'}, ${created} new candidate${created === 1 ? '' : 's'} staged. \`lit fetch ${lib.manifest.id}\` gets their text.` };
        if (json) return out({ ...delta, papers: hits }), 0;
        for (const h of hits) out(`${(h.year ?? '').toString().padEnd(5)} ${h.openAccess ? 'OA ' : '   '} ${h.title}${h.doi ? `  doi:${h.doi}` : ''}`);
        return out(`\n${delta.message}`), 0;
      }

      case 'add': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const what = rest[1];
        if (!what) throw new Error('Add what? A DOI, a PMID, a PMCID, or a PDF.');
        if (/\.pdf$/i.test(what)) {
          if (!existsSync(what)) throw new Error(`No file at ${what}.`);
          mkdirSync(lib.inboxDir, { recursive: true });
          copyFileSync(what, join(lib.inboxDir, basename(what)));
          const delta = { ok: true as const, message: `Copied ${basename(what)} into the inbox. \`lit ingest ${lib.manifest.id}\` reads it.` };
          return out(json ? delta : delta.message), 0;
        }
        const paper = await lookupEuropePmc(what);
        if (!paper) throw new Error(`Europe PMC knows nothing by "${what}".`);
        const db = openDb(lib.dbPath);
        try {
          const { key, created } = upsertPaper(db, paper, now);
          const delta = { ok: true as const, key, created, message: `${created ? 'Staged' : 'Already had'} ${key}: ${paper.title}${paper.pmcid ? ' (open access)' : ' (no full text at Europe PMC)'}.` };
          return out(json ? delta : delta.message), 0;
        } finally {
          db.close();
        }
      }

      case 'fetch': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const report = await fetchCandidates(lib, { log, now });
        const delta = { ok: true as const, ...report, message: `Fetched ${report.fetched.length}; ${report.needsPdf.length} need a PDF. \`lit ingest ${lib.manifest.id}\` reads them.` };
        return out(json ? delta : delta.message), 0;
      }

      case 'ingest': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const embedder = await embedderFor(root, lib, flags);
        const report = await ingestLibrary(lib, embedder, { log, now, reread: flags['reread'] === true });
        const delta = { ok: true as const, ...report, message: `Ingested ${report.ingested.length} paper${report.ingested.length === 1 ? '' : 's'} (${report.inbox.length} from the inbox), embedded ${report.embedded} chunk${report.embedded === 1 ? '' : 's'}${report.failed.length ? `; ${report.failed.length} failed` : ''}.` };
        return out(json ? delta : delta.message), 0;
      }

      case 'refresh': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        let staged = 0;
        const db = openDb(lib.dbPath);
        try {
          for (const query of lib.manifest.queries) {
            const hits = await searchEuropePmc(query, { limit: 50 });
            for (const hit of hits) if (upsertPaper(db, hit, now).created) staged += 1;
            log(`searched "${query}": ${hits.length} hits`);
          }
        } finally {
          db.close();
        }
        const fetched = await fetchCandidates(lib, { log, now });
        const embedder = await embedderFor(root, lib, flags);
        const ingested = await ingestLibrary(lib, embedder, { log, now });
        const annotated = await annotateLibrary(lib, { log, now });
        let extracted = 0;
        if (lib.manifest.extract === 'ollama') {
          const health = await ollamaHealth(lib.manifest.ollama, [lib.manifest.ollama.chat]);
          if (health.reachable && !health.missing.length) extracted = (await extractLibrary(lib, extractorFor(lib), { log, now })).extracted.length;
          else log(`Note: Ollama ${health.reachable ? `has no model "${lib.manifest.ollama.chat}"` : 'is not reachable'}; the model stage waits. \`lit extract ${lib.manifest.id}\` runs it later.`);
        }
        const delta = { ok: true as const, staged, fetched: fetched.fetched.length, needsPdf: fetched.needsPdf.length, ingested: ingested.ingested.length, embedded: ingested.embedded, annotated: annotated.annotated.length, extracted, message: `${staged} new candidate${staged === 1 ? '' : 's'}, ${fetched.fetched.length} fetched, ${ingested.ingested.length} ingested, ${ingested.embedded} chunks embedded${lib.manifest.extract === 'ollama' ? `, ${extracted} read by the model` : ''}.` };
        return out(json ? delta : delta.message), 0;
      }

      case 'status': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const db = openDb(lib.dbPath);
        try {
          const status = statusView(db, lib.manifest.model);
          if (json) return out({ library: lib.manifest, ...status }), 0;
          out(`${lib.manifest.name}  ${lib.manifest.id}${lib.manifest.projectId ? `  project ${lib.manifest.projectId}` : ''}`);
          out(`  ${status.papers.ingested} ingested · ${status.papers.fetched} fetched, not yet read · ${status.papers.candidate} candidates · ${status.papers['needs-pdf']} need a PDF`);
          out(`  ${status.chunks} chunks, ${status.vectors} embedded (${lib.manifest.model}), ${status.parameters} parameters`);
          if (lib.manifest.extract === 'ollama') out(`  model stage (${lib.manifest.ollama.chat}): ${status.extracted}/${status.papers.ingested} papers read — ${status.claims} claims, ${status.materials} materials, ${status.methods} methods`);
          out(`  graph: ${status.annotated}/${status.papers.ingested} papers annotated, ${status.entities} entities, ${status.mentions} mentions`);
          if (lib.manifest.includes.length) out(`  includes: ${lib.manifest.includes.join(', ')}`);
          if (lib.manifest.queries.length) out(`  searches: ${lib.manifest.queries.map((q) => `"${q}"`).join(', ')}`);
          if (status.needsPdf.length) {
            out('\n  need a PDF (drop it in the inbox):');
            for (const p of status.needsPdf) out(`    ${p.year ?? '    '}  ${p.title}${p.doi ? `  doi:${p.doi}` : ''}`);
          }
          return 0;
        } finally {
          db.close();
        }
      }

      case 'wanted': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const rows = runSql(lib, "select key, year, title, journal, doi, pmid, cited_by_count, pub_type from papers where status = 'needs-pdf' order by cited_by_count desc, year desc", 1000).rows as { key: string; year: number | null; title: string; journal: string | null; doi: string | null; pmid: string | null; cited_by_count: number | null; pub_type: string | null }[];
        const link = (r: typeof rows[number]) => (r.doi ? `https://doi.org/${r.doi}` : r.pmid ? `https://pubmed.ncbi.nlm.nih.gov/${r.pmid}/` : '');
        const csv = one(flags['csv']);
        if (csv) {
          const q = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`;
          const lines = ['year,cited_by,type,title,journal,doi,link', ...rows.map((r) => [r.year, r.cited_by_count, r.pub_type?.includes('review') ? 'review' : 'article', r.title, r.journal, r.doi, link(r)].map(q).join(','))];
          writeFileSync(csv, `${lines.join('\n')}\n`);
        }
        if (json) return out(rows.map((r) => ({ ...r, link: link(r) }))), 0;
        if (!rows.length) return out('Nothing is waiting for a PDF.'), 0;
        out(`${rows.length} paper${rows.length === 1 ? '' : 's'} waiting for a PDF — drop them in ${lib.inboxDir}; the file name does not matter, ingest reads the DOI off the page.\n`);
        for (const r of rows) out(`${String(r.cited_by_count ?? 0).padStart(4)} cited  ${(r.year ?? '').toString().padEnd(5)} ${r.pub_type?.includes('review') ? 'review ' : '       '} ${r.title}\n             ${link(r)}`);
        if (csv) out(`\nWrote ${csv}.`);
        return 0;
      }

      case 'papers': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const db = openDb(lib.dbPath);
        try {
          const papers = allPapers(db);
          if (json) return out(papers), 0;
          for (const p of papers) out(`${p.status.padEnd(10)} ${(p.year ?? '').toString().padEnd(5)} ${(p.pub_type?.includes('review') ? 'review' : p.pub_type ? 'article' : '').padEnd(8)} ${p.key.padEnd(34)} ${p.title}`);
          if (!papers.length) out('No papers yet.');
          return 0;
        } finally {
          db.close();
        }
      }

      case 'query': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const question = rest.slice(1).join(' ').trim();
        if (!question) throw new Error('Ask what? lit query <lib> <question>');
        const embedder = await embedderFor(root, lib, flags);
        const limit = one(flags['limit']);
        const trace = { seeds: {} as Record<string, string[]> };
        const hits = await queryLibrary(lib, question, embedder, { limit: limit ? Number(limit) : 8, spine: flags['no-spine'] !== true, graph: flags['no-graph'] !== true, trace });
        if (json) return out(flags['trace'] === true ? { hits, trace } : hits), 0;
        if (flags['trace'] === true) {
          for (const [id, seeds] of Object.entries(trace.seeds)) out(`graph seeds (${id}): ${seeds.length ? seeds.join(', ') : 'none named — the walk starts from the nearest passages'}`);
          out('');
        }
        if (!hits.length) return out('Nothing in the library speaks to that.'), 0;
        hits.forEach((h, i) => {
          const via = flags['trace'] === true ? `  {${Object.entries(h.ranks).map(([k, v]) => `${k} #${v}`).join(', ')}}` : '';
          out(`${i + 1}. ${h.citation}${h.library !== lib.manifest.id ? `  [${h.library}]` : ''}${via}`);
          out(`   ${h.text.length > 600 ? `${h.text.slice(0, 600)}…` : h.text}\n`);
        });
        return 0;
      }

      case 'sql': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const sql = rest.slice(1).join(' ');
        const limit = one(flags['limit']);
        const result = runSql(lib, sql, limit ? Number(limit) : 200);
        if (json) return out(result.rows), 0;
        if (!result.rows.length) return out('No rows.'), 0;
        out(result.columns.join('\t'));
        for (const row of result.rows) out(result.columns.map((c) => String(row[c] ?? '')).join('\t'));
        return 0;
      }

      case 'snowball': {
        const lib = need(openLibrary(root, rest[0] ?? ''), rest[0]);
        const key = rest[1];
        if (!key) throw new Error('Snowball from which paper? Give its key (`lit papers` lists them).');
        const db = openDb(lib.dbPath);
        try {
          const paper = db.prepare('SELECT * FROM papers WHERE key = ?').get(key) as { pmid: string | null; pmcid: string | null } | undefined;
          if (!paper) throw new Error(`No paper ${key} in the library.`);
          const [source, id] = paper.pmid ? ['MED', paper.pmid] : paper.pmcid ? ['PMC', paper.pmcid] : [null, null];
          if (!source || !id) throw new Error('That paper has no PMID or PMCID, so Europe PMC has no reference list for it.');
          const refs = await referencesOf(source, id);
          let staged = 0;
          for (const ref of refs) {
            if (!ref.doi && !ref.pmid) continue;
            if (upsertPaper(db, { doi: ref.doi, pmid: ref.pmid, title: ref.title, source: 'europepmc:snowball' }, now).created) staged += 1;
          }
          const delta = { ok: true as const, references: refs.length, staged, message: `${refs.length} references, ${staged} new candidate${staged === 1 ? '' : 's'} staged. \`lit fetch ${lib.manifest.id}\` gets their text.` };
          return out(json ? delta : delta.message), 0;
        } finally {
          db.close();
        }
      }

      default:
        throw new Error(`Unknown command "${command}". Run "lit help" for the list.`);
    }
  } catch (error) {
    const failure = error instanceof Error ? error : new Error(String(error));
    const code = (failure as { code?: string }).code ?? 'error';
    if (json) out({ ok: false, code, message: failure.message });
    else process.stderr.write(`${failure.message}\n`);
    return 1;
  }
}

process.stdout.on('error', (error: NodeJS.ErrnoException) => {
  if (error.code === 'EPIPE') process.exit(0);
  throw error;
});

process.exitCode = await main(process.argv.slice(2));
