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

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const operations = JSON.parse(readFileSync(new URL('../plugin/endbot/src/endstone_endbot/operations.json', import.meta.url), 'utf8'));
  const target = new URL('../docs/SCRIPTING.md', import.meta.url);
  const before = readFileSync(target, 'utf8');
  const after = before.replace(/(<!-- BEGIN GENERATED OPERATIONS -->\r?\n)[\s\S]*?(<!-- END GENERATED OPERATIONS -->)/,
    (_, begin, end) => begin + reference(operations) + end);
  if (process.argv.includes('--check')) {
    if (before !== after) throw new Error('docs/SCRIPTING.md operation reference is out of date');
  } else writeFileSync(target, after);
}
