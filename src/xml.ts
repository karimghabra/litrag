/**
 * Enough XML to read JATS: a tokenizer and a tree, with no dependency.
 *
 * Europe PMC's full text is well-formed XML with a known vocabulary, and
 * Node ships no DOM parser. This reads elements, attributes, text, CDATA and
 * the entities JATS uses; it does not validate and it does not resolve
 * namespaces, because reading an article needs neither.
 */

export interface XmlElement {
  name: string;
  attrs: Record<string, string>;
  children: (XmlElement | string)[];
}

const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };

export function decodeEntities(text: string): string {
  return text.replace(/&(#x[0-9a-fA-F]+|#\d+|[a-zA-Z]+);/g, (whole, body: string) => {
    if (body.startsWith('#x')) return String.fromCodePoint(parseInt(body.slice(2), 16));
    if (body.startsWith('#')) return String.fromCodePoint(parseInt(body.slice(1), 10));
    return ENTITIES[body] ?? whole;
  });
}

const ATTR = /([^\s=/>]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;

function parseAttrs(source: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of source.matchAll(ATTR)) out[m[1]!] = decodeEntities(m[2] ?? m[3] ?? '');
  return out;
}

export function parseXml(xml: string): XmlElement {
  const root: XmlElement = { name: '#document', attrs: {}, children: [] };
  const stack: XmlElement[] = [root];
  let i = 0;
  const n = xml.length;
  while (i < n) {
    const lt = xml.indexOf('<', i);
    if (lt < 0) {
      appendText(stack, xml.slice(i));
      break;
    }
    if (lt > i) appendText(stack, xml.slice(i, lt));
    if (xml.startsWith('<!--', lt)) {
      const end = xml.indexOf('-->', lt + 4);
      i = end < 0 ? n : end + 3;
      continue;
    }
    if (xml.startsWith('<![CDATA[', lt)) {
      const end = xml.indexOf(']]>', lt + 9);
      const text = xml.slice(lt + 9, end < 0 ? n : end);
      stack[stack.length - 1]!.children.push(text);
      i = end < 0 ? n : end + 3;
      continue;
    }
    if (xml.startsWith('<?', lt) || xml.startsWith('<!', lt)) {
      const end = xml.indexOf('>', lt);
      i = end < 0 ? n : end + 1;
      continue;
    }
    const gt = findTagEnd(xml, lt);
    const body = xml.slice(lt + 1, gt);
    i = gt + 1;
    if (body.startsWith('/')) {
      const name = body.slice(1).trim();
      // Close up to the matching element; a stray close tag is ignored.
      for (let d = stack.length - 1; d > 0; d--) {
        if (stack[d]!.name === name) {
          stack.length = d;
          break;
        }
      }
      continue;
    }
    const selfClosing = body.endsWith('/');
    const inner = selfClosing ? body.slice(0, -1) : body;
    const nameEnd = inner.search(/[\s]/);
    const name = (nameEnd < 0 ? inner : inner.slice(0, nameEnd)).trim();
    const element: XmlElement = { name, attrs: nameEnd < 0 ? {} : parseAttrs(inner.slice(nameEnd)), children: [] };
    stack[stack.length - 1]!.children.push(element);
    if (!selfClosing) stack.push(element);
  }
  return root;
}

/** The `>` that ends a tag, skipping any inside quoted attribute values. */
function findTagEnd(xml: string, from: number): number {
  let quote: string | null = null;
  for (let j = from + 1; j < xml.length; j++) {
    const ch = xml[j]!;
    if (quote) {
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
    } else if (ch === '>') {
      return j;
    }
  }
  return xml.length - 1;
}

function appendText(stack: XmlElement[], raw: string): void {
  if (!raw) return;
  stack[stack.length - 1]!.children.push(decodeEntities(raw));
}

export function isElement(node: XmlElement | string): node is XmlElement {
  return typeof node !== 'string';
}

/** Every descendant element with the name, in document order. */
export function findAll(node: XmlElement, name: string): XmlElement[] {
  const out: XmlElement[] = [];
  const walk = (el: XmlElement) => {
    for (const child of el.children) {
      if (!isElement(child)) continue;
      if (child.name === name) out.push(child);
      walk(child);
    }
  };
  walk(node);
  return out;
}

export function findFirst(node: XmlElement, name: string): XmlElement | undefined {
  for (const child of node.children) {
    if (!isElement(child)) continue;
    if (child.name === name) return child;
    const deeper = findFirst(child, name);
    if (deeper) return deeper;
  }
  return undefined;
}

export function childElements(node: XmlElement, name?: string): XmlElement[] {
  return node.children.filter(isElement).filter((c) => name === undefined || c.name === name);
}

/** The text of a node with whitespace collapsed, leaving out the elements named. */
export function textOf(node: XmlElement | string, skip: ReadonlySet<string> = new Set()): string {
  const parts: string[] = [];
  const walk = (n: XmlElement | string) => {
    if (!isElement(n)) {
      parts.push(n);
      return;
    }
    if (skip.has(n.name)) return;
    for (const child of n.children) walk(child);
  };
  walk(node);
  return parts.join('').replace(/\s+/g, ' ').trim();
}
