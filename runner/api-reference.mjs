import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

export function reference(operations) {
  const type = spec => spec.choices ? spec.choices.join(' / ') : spec.type;
  const rows = ['| Method | Positional fields | Final options table |', '| --- | --- | --- |'];
  for (const [operation, variants] of Object.entries(operations)) {
    for (const shape of variants) {
      const positional = shape.positional.map(([key, spec]) => `${key}: ${type(spec)}`).join(', ') || 'none';
      const options = Object.entries(shape.options).map(([key, spec]) => `${key}: ${type(spec)}`).join(', ') || 'none';
      rows.push(`| \`:${operation}()\` | ${positional} | ${options} |`);
    }
  }
  return rows.join('\n') + '\n';
}

// The same sentence operations.guarantee_text states in Python; the two tests share one fixture string.
export function guaranteeText(spec) {
  const guarantee = spec.guarantee ?? spec;
  if (guarantee.class === 'each') {
    return 'At most once, after the fact, in source order. '
      + 'Every occurrence is delivered while the source is connected.';
  }
  const causes = guarantee.each ?? [];
  const list = causes.length < 2 ? (causes[0] ?? '')
    : `${causes.slice(0, -1).join(', ')} and ${causes.at(-1)}`;
  let text = `A change that lasts at least ${guarantee.ticks} ticks is delivered`;
  if (causes.length) text += `; ${list} ${causes.length === 1 ? 'is' : 'are'} delivered individually`;
  return `At most once, after the fact, in source order. ${text}.`;
}

export function events(eventsTable) {
  const entries = Object.entries(eventsTable);
  if (!entries.length) return 'No event kinds are listed yet.\n';
  const filter = (filters, name) => `${name}: ${filters[name].type}`
    + (filters[name].maxBlocks ? ` (max ${filters[name].maxBlocks} blocks)` : '');
  const rows = ['| Kind | Filters (one of) | Payload | Guarantee |', '| --- | --- | --- | --- |'];
  for (const [kind, spec] of entries) {
    const filters = spec.require.length ? spec.require.map(names => names.map(name => filter(spec.filters, name))
      .join(', ')).join(' or ') : 'none';
    const payload = Object.entries(spec.payload).map(([name, type]) => `${name}: ${type}`).join(', ') || 'none';
    rows.push(`| \`${kind}\` | ${filters} | ${payload} | ${guaranteeText(spec)} |`);
  }
  return rows.join('\n') + '\n';
}

function fill(text, name, body) {
  const pattern = new RegExp(`(<!-- BEGIN GENERATED ${name} -->\\r?\\n)[\\s\\S]*?(<!-- END GENERATED ${name} -->)`);
  if (!pattern.test(text)) throw new Error(`docs/SCRIPTING.md has no generated ${name.toLowerCase()} region`);
  return text.replace(pattern, (_, begin, end) => begin + body + end);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const registry = JSON.parse(readFileSync(new URL('../core/src/endbot_core/operations.json', import.meta.url), 'utf8'));
  const target = new URL('../docs/SCRIPTING.md', import.meta.url);
  const before = readFileSync(target, 'utf8');
  const after = fill(fill(before, 'OPERATIONS', reference(registry.operations)), 'EVENTS', events(registry.events));
  if (process.argv.includes('--check')) {
    if (before !== after) throw new Error('docs/SCRIPTING.md generated reference is out of date');
  } else writeFileSync(target, after);
}
