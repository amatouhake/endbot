import { readFile } from 'node:fs/promises';
import { startRun } from './host.mjs';

const source = await readFile(process.argv[2] || new URL('./examples/interleaved.luau', import.meta.url), 'utf8');
const args = process.argv[3] ? JSON.parse(process.argv[3]) : {};
const run = startRun(source, args, {
  onOperation: async request => console.log('mock host accepted:', JSON.stringify(request)),
});
console.log('declarations:', JSON.stringify(await run.ready));
const result = await run.done;
console.log(JSON.stringify({ result, events: run.events }, null, 2));
if (result.type !== 'done') process.exitCode = 1;
