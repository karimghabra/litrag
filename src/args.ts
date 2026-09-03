/**
 * Reading argv.
 *
 * Kept apart from bin.ts because bin.ts runs whatever command it was handed
 * the moment it is imported, and a parser that cannot be imported cannot be
 * tested.
 */

export interface Args {
  positional: string[];
  /** A value, a bare switch, or — for a flag given more than once — every value it was given. */
  flags: Record<string, string | boolean | string[]>;
}

/**
 * Flags that never take a value.
 *
 * Without this list a switch swallows whatever follows it, and the usage line
 * documents the order that breaks: `pt --vault DIR --json cultures` gave
 * `--json` the value "cultures", left no verb behind, printed the help and
 * exited 0. A read that quietly returns nothing at all is the worst thing a
 * command-line tool can do to a script, and to anything reading it.
 */
const SWITCHES = new Set(['json', 'help', 'experiment', 'undated', 'preview', 'merge', 'yes', 'new', 'undo']);

/** A flag given more than once, or once, or not at all. */
export function list(value: string | boolean | string[] | undefined): string[] {
  if (typeof value === 'string') return [value];
  return Array.isArray(value) ? value : [];
}

/** A flag meant to be given once. If it was repeated anyway, the last one wins. */
export function one(value: string | boolean | string[] | undefined): string | undefined {
  return list(value).at(-1);
}

export function parseArgs(argv: string[]): Args {
  const positional: string[] = [];
  const flags: Args['flags'] = {};

  /*
    A value-flag given again accumulates rather than overwrites. The recipe
    verbs advertise repetition — `--takes a:1 --takes b:2` — and keeping only
    the last one recorded a two-ingredient recipe as one, silently. A switch
    given again is still just true, and a value-flag left bare adds nothing
    rather than erasing what an earlier one said.
  */
  const record = (name: string, value: string | boolean): void => {
    const prior = flags[name];
    if (prior === undefined) {
      flags[name] = value;
      return;
    }
    if (typeof value !== 'string') return;
    if (Array.isArray(prior)) prior.push(value);
    else if (typeof prior === 'string') flags[name] = [prior, value];
    else flags[name] = value;
  };

  for (let i = 0; i < argv.length; i++) {
    const token = argv[i]!;
    if (!token.startsWith('--')) {
      positional.push(token);
      continue;
    }
    const name = token.slice(2);
    const next = argv[i + 1];
    if (!SWITCHES.has(name) && next !== undefined && !next.startsWith('--')) {
      record(name, next);
      i += 1;
    } else {
      record(name, true);
    }
  }
  return { positional, flags };
}
