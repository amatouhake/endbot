import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { startRun, executable } from './host.mjs';

test('declares parameters before body, interleaves two bots, waits explicitly', { timeout: 10000 }, async () => {
  const source = await readFile(new URL('./examples/interleaved.luau', import.meta.url), 'utf8');
  const accepted = [];
  const run = startRun(source, { first: 'One', second: 'Two', ticks: 3 }, {
    pauseBeforeStart: true, limits: { tickMs: 1 },
    onOperation: async event => accepted.push([event.bot, event.operation]),
  });
  const declaration = await run.ready;
  assert.equal(declaration.type, 'params');
  assert.equal(declaration.schema.first.default, 'Alice');
  assert.equal(run.events.length, 1);
  run.begin();
  assert.equal((await run.done).type, 'done');
  assert.deepEqual(accepted, [['One', 'move'], ['Two', 'move'], ['One', 'move'], ['Two', 'look']]);
  assert.deepEqual(run.events.filter(e => e.type === 'wait').map(e => e.ticks), [1, 1, 3]);
  assert.deepEqual(run.events.filter(e => e.id).map(e => e.id), [1, 2, 3, 4, 5, 6, 7, 8]);
});

test('host call suspends script until acceptance', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  let observed;
  const received = new Promise(resolve => { observed = resolve; });
  const run = startRun('params{}\nbot("A"):look(0, 0)\nlog("after")', {}, {
    onOperation: async () => { observed(); await pending; },
  });
  await received;
  assert.equal(run.events.some(e => e.type === 'log'), false);
  release();
  assert.equal((await run.done).type, 'done');
  assert.equal(run.events.find(e => e.type === 'log').text, 'after');
});

test('authorization is rechecked after a wait; rejection ends script', async () => {
  let permitted = true, checks = 0, applied = 0;
  const run = startRun('params{}\nbot("A"):move("forward", {ticks=2})\nwait(1)\nbot("A"):look(0, 0)\nlog("wrong")', {}, {
    authorize: () => { checks++; return permitted; },
    onOperation: () => { applied++; },
    onWait: () => { permitted = false; },
  });
  const result = await run.done;
  assert.equal(result.type, 'error');
  assert.match(result.message, /authorization revoked/);
  assert.equal(checks, 2);
  assert.equal(applied, 1);
  assert.equal(run.events.some(e => e.type === 'log'), false);
});

test('infinite loop interrupted inside VM, including pcall attempts to swallow errors', async () => {
  for (const code of ['while true do end', 'while true do pcall(function() while true do end end) end']) {
    const run = startRun(`params{}\n${code}`, {}, { limits: { interruptBudget: 1000, sliceMs: 1000 } });
    const result = await run.done;
    assert.equal(result.type, 'error');
    assert.equal(result.phase, 'execute');
    assert.match(result.message, /execution budget exceeded/);
    assert.equal(result.signal, null);
  }
});

test('elapsed VM slice limit also interrupts an infinite loop', async () => {
  const run = startRun('params{}\nwhile true do end', {}, { limits: { interruptBudget: 100000000, sliceMs: 1 } });
  const result = await run.done;
  assert.equal(result.phase, 'execute');
  assert.match(result.message, /execution budget exceeded/);
});

test('host stop ends a waiting script without later operations', async () => {
  let run;
  run = startRun('params{}\nwait(100)\nbot("A"):look(0,0)', {}, {
    onWait: () => { run.stop(); return new Promise(() => {}); },
  });
  const result = await run.done;
  assert.equal(result.type, 'stopped');
  assert.equal(result.id, 'host-stop');
  assert.equal(run.events.some(e => e.type === 'operation'), false);
});

test('host watchdog forcibly ends CPU-bound runner independently of VM budget', async () => {
  const run = startRun('params{}\nwhile true do end', {}, { limits: { wallMs: 200, sliceMs: 60000, interruptBudget: 100000000 } });
  const result = await run.done;
  assert.equal(result.phase, 'host');
  assert.match(result.message, /wall time budget/);
});

test('host stop forcibly ends a CPU-bound script', async () => {
  const run = startRun('params{}\nwhile true do end', {}, {
    pauseBeforeStart: true, limits: { sliceMs: 60000, interruptBudget: 100000000 },
  });
  assert.equal((await run.ready).type, 'params');
  run.begin();
  setTimeout(run.stop, 10);
  const result = await run.done;
  assert.equal(result.type, 'stopped');
  assert.equal(result.phase, 'host');
});

test('filesystem, OS, loader and private bridge globals unavailable', async () => {
  const run = startRun(`params{}
assert(io == nil and os == nil and require == nil and package == nil)
assert(loadfile == nil and dofile == nil and loadstring == nil)
assert(_exchange == nil and _log == nil and coroutine == nil)
log("isolated")`);
  assert.equal((await run.done).type, 'done');
  assert.equal(run.events.find(e => e.type === 'log').text, 'isolated');
});

test('syntax error retains source line number', async () => {
  const run = startRun('params{}\nlocal x = 1\nlocal broken = )');
  const result = await run.done;
  assert.equal(result.type, 'error');
  assert.equal(result.phase, 'compile');
  assert.match(result.message, /script:3:/);
});

test('bad arguments and host errors do not continue execution', async () => {
  const defaulted = startRun('local a=params{n={type="number",default=3},s={type="string",default="ok"},b={type="boolean",default=false}}\nassert(a.n==3 and a.s=="ok" and a.b==false)\nlog("defaults")');
  assert.equal((await defaulted.done).type, 'done');
  const badArgs = startRun('local a=params{n={type="number"}}\nlog("wrong")', { n: 'bad' });
  assert.match((await badArgs.done).message, /invalid argument: n/);
  const rejected = startRun('params{}\nbot("A"):look(0,0)\nlog("wrong")', {}, {
    onOperation: () => { throw new Error('mock host unavailable'); },
  });
  assert.match((await rejected.done).message, /mock host unavailable/);
  assert.equal(rejected.events.some(e => e.type === 'log'), false);
});

test('bounded host replies, memory allocation, and output', async () => {
  const missingReply = startRun('params{}\nbot("A"):look(0,0)', {}, {
    limits: { requestMs: 50 }, onOperation: () => new Promise(() => {}),
  });
  assert.match((await missingReply.done).message, /host request timeout/);
  const memory = startRun('params{}\nlocal t={}\nfor i=1,1000000 do t[i]=string.rep("x",100)..tostring(i) end', {}, {
    limits: { memoryBytes: 1024 * 1024, sliceMs: 5000, interruptBudget: 100000000 },
  });
  assert.match((await memory.done).message, /memory/i);
  const output = startRun('params{}\nfor i=1,100 do log("x") end', {}, { limits: { maxMessages: 10 } });
  assert.match((await output.done).message, /message budget/);
});

test('native protocol rejects stale reply ids and host EOF', async () => {
  const bootstrap = await readFile(new URL('./api.luau', import.meta.url), 'utf8');
  for (const mode of ['stale', 'eof']) {
    const child = spawn(executable, [], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    const events = [];
    let buffer = '';
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', chunk => {
      buffer += chunk;
      let end;
      while ((end = buffer.indexOf('\n')) !== -1) {
        const event = JSON.parse(buffer.slice(0, end)); buffer = buffer.slice(end + 1);
        events.push(event);
        if (event.type === 'params') {
          if (mode === 'stale') child.stdin.end(JSON.stringify({ type: 'reply', id: 999, accepted: true, args: {} }) + '\n');
          else child.stdin.end();
        }
      }
    });
    child.stderr.resume();
    const closed = new Promise((resolve, reject) => { child.on('close', resolve); child.on('error', reject); });
    const timeout = setTimeout(() => child.kill(), 3000);
    child.stdin.write(JSON.stringify({ type: 'load', source: 'params{}\nlog("wrong")', bootstrap }) + '\n');
    assert.equal(await closed, 1);
    clearTimeout(timeout);
    assert.equal(events.at(-1).phase, 'protocol');
    assert.match(events.at(-1).message, mode === 'stale' ? /stale reply/ : /host EOF/);
    assert.equal(events.some(e => e.type === 'log'), false);
  }
});
