import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { executable } from './host.mjs';

const linux = process.platform === 'linux';
const flags = ({ memory = 256 * 1024 * 1024, cpu = 0, parent = process.pid } = {}) => [
  '--max-address-space', String(memory), '--max-cpu-seconds', String(cpu),
  '--parent-pid', String(parent), '--nice', '10',
];
const bootstrap = await readFile(new URL('./api.luau', import.meta.url), 'utf8');
const load = (source, limits = {}) => JSON.stringify({ type: 'load', source, bootstrap, limits }) + '\n';

function launch(t, args) {
  const child = spawn(executable, args, { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  t.after(() => { if (child.exitCode === null && child.signalCode === null) child.kill(); });
  let stdout = '', stderr = '';
  child.stdout.setEncoding('utf8').on('data', data => { stdout += data; });
  child.stderr.setEncoding('utf8').on('data', data => { stderr += data; });
  // An early startup rejection can close stdin before a load write completes.
  child.stdin.on('error', error => { if (error.code !== 'EPIPE') throw error; });
  const closed = once(child, 'close').then(([code, signal]) => ({
    code, signal, stderr, events: stdout.trim().split('\n').filter(Boolean).map(line => JSON.parse(line)),
  }));
  return { child, closed };
}

test('invalid CLI arguments fail before reading stdin, with one NDJSON error', { timeout: 10000 }, async t => {
  const invalid = [
    ['--unknown'], ['--unknown"\n'], ['positional'], ['--nice'], ['--max-address-space', '123'],
    [...flags(), '--nice', '10'],
  ];
  for (const flag of ['--max-address-space', '--max-cpu-seconds', '--parent-pid', '--nice']) {
    for (const value of ['', '-1', '+1', ' 1', '1 ', '1x', '1.5', '18446744073709551616']) {
      const args = flags(); args[args.indexOf(flag) + 1] = value; invalid.push(args);
    }
  }
  for (const [flag, value] of [
    ['--parent-pid', '0'], ['--parent-pid', '2147483648'], ['--nice', '0'], ['--nice', '11'],
    ['--max-cpu-seconds', '2147483648'], ['--max-address-space', '18446744073709551615'],
  ]) {
    const args = flags(); args[args.indexOf(flag) + 1] = value; invalid.push(args);
  }
  for (const args of invalid) {
    // Keep stdin open and empty: successful exit cannot depend on EOF or a load frame.
    const { closed } = launch(t, args);
    const result = await closed;
    assert.equal(result.code, 1, args.join(' '));
    assert.equal(result.signal, null);
    assert.equal(result.events.length, 1);
    assert.equal(result.events[0].type, 'error');
    assert.equal(result.events[0].phase, 'confinement');
    assert.match(result.events[0].message, /^confinement failed:/);
  }
});

test('no CLI flags allows the standalone harness and explicit OS opt-out', { timeout: 5000 }, async t => {
  const { child, closed } = launch(t, []);
  child.stdin.end(load('local x = 1'));
  const result = await closed;
  assert.equal(result.code, 0);
  assert.deepEqual(result.events, [{ type: 'done' }]);
});

test('Windows accepts but ignores Linux confinement flags', { skip: process.platform !== 'win32', timeout: 5000 }, async t => {
  const { child, closed } = launch(t, flags({ memory: 1, cpu: 1, parent: 1 }));
  child.stdin.end(load('local x = 1'));
  const result = await closed;
  assert.equal(result.code, 0);
  assert.deepEqual(result.events, [{ type: 'done' }]);
});

test('Linux parent PID race fails closed before stdin', { skip: !linux, timeout: 5000 }, async t => {
  const { closed } = launch(t, flags({ parent: process.pid + 1 }));
  const result = await closed;
  assert.equal(result.code, 1);
  assert.equal(result.events.length, 1);
  assert.match(result.events[0].message, /^confinement failed: parent PID changed$/);
});

test('Linux applies nice +10, RLIMIT_AS and optional RLIMIT_CPU before input', { skip: !linux, timeout: 5000 }, async t => {
  const { child, closed } = launch(t, flags({ memory: 64 * 1024 * 1024, cpu: 2 }));
  // A params event proves startup has completed; the runner then waits for a reply.
  child.stdin.write(load('params{}'));
  let output = '';
  for await (const chunk of child.stdout.iterator({ destroyOnReturn: false })) {
    output += chunk; if (output.includes('\n')) break;
  }
  assert.equal(JSON.parse(output.trim()).type, 'params');
  const limits = await readFile(`/proc/${child.pid}/limits`, 'utf8');
  assert.match(limits, /Max address space\s+67108864\s+67108864\s+bytes/);
  assert.match(limits, /Max cpu time\s+2\s+3\s+seconds/);
  const stat = await readFile(`/proc/${child.pid}/stat`, 'utf8');
  const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
  assert.ok(Number(fields[16]) >= 10, `nice was ${fields[16]}`); // field 19, after pid/comm.
  child.kill();
  await closed;
});

test('Linux process cap rejects an allocation that fits the VM allocator bound', { skip: !linux, timeout: 10000 }, async t => {
  const source = 'local s = string.rep("x", 96 * 1024 * 1024)';
  for (const [memory, success] of [[0, true], [64 * 1024 * 1024, false]]) {
    const { child, closed } = launch(t, flags({ memory }));
    child.stdin.end(load(source, { memoryBytes: 384 * 1024 * 1024, sliceMs: 5000 }));
    const result = await closed;
    assert.equal(result.signal, null);
    assert.equal(result.code, 0);
    assert.equal(result.events.at(-1).type, success ? 'done' : 'error');
    if (!success) assert.match(result.events.at(-1).message, /memory/i);
  }
});

test('Linux cumulative CPU cap interrupts before the VM slice budget', { skip: !linux, timeout: 10000 }, async t => {
  const { child, closed } = launch(t, flags({ cpu: 1 }));
  child.stdin.end(load('while true do end', { sliceMs: 60000, interruptBudget: 2147483647 }));
  const result = await closed;
  assert.equal(result.signal, 'SIGXCPU');
  assert.deepEqual(result.events, []);
});

test('Linux parent death kills the runner after confinement is installed', { skip: !linux, timeout: 15000 }, async t => {
  // A dedicated native subreaper reaps the orphan, even when CI's PID 1 does not.
  // This helper does not load Python or alter the Node test process's subreaper state.
  const directory = await mkdtemp(path.join(tmpdir(), 'endbot-pdeath-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const probe = path.join(directory, 'parent-death');
  const source = fileURLToPath(new URL('./tests/parent-death.cpp', import.meta.url));
  const built = spawnSync(process.env.CXX || 'c++', ['-std=c++17', source, '-o', probe], { encoding: 'utf8', timeout: 10000 });
  assert.equal(built.status, 0, built.error?.message || built.stderr);
  const result = spawnSync(probe, [executable], { input: load('params{}'), encoding: 'utf8', timeout: 7000 });
  assert.equal(result.status, 0, result.error?.message || result.stderr);
  assert.equal(result.stdout.trim(), 'parent-death killed');
});
