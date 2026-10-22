import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { startRun, executable, mockOperations, parameters } from './host.mjs';
import { reference } from './api-reference.mjs';

test('declares parameters before body, interleaves two bots, waits explicitly', { timeout: 10000 }, async () => {
  const source = await readFile(new URL('./examples/interleaved.luau', import.meta.url), 'utf8');
  const accepted = [];
  const run = startRun(source, { first: 'One', second: 'Two', ticks: 3 }, {
    pauseBeforeStart: true, limits: { tickMs: 1 },
    onOperation: async event => accepted.push([event.target, event.operation]),
  });
  const declaration = await run.ready;
  assert.equal(declaration.type, 'params');
  assert.deepEqual(declaration.schema.map(spec => spec.name), ['first', 'second', 'ticks']);
  assert.equal(declaration.schema[0].default, 'Alice');
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
  assert.equal(await Promise.race([received.then(() => true), run.done.then(() => false)]), true,
    'runner ended before the expected operation');
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
  const defaulted = startRun('local a=params{param.number("n",3),param.string("s","ok"),param.bool("b",false)}\nassert(a.n==3 and a.s=="ok" and a.b==false)\nlog("defaults")');
  assert.equal((await defaulted.done).type, 'done');
  const badArgs = startRun('local a=params{param.number "n"}\nlog("wrong")', { n: 'bad' });
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

test('all production operations and positional overloads are generated from the shared table', async () => {
  const received = [];
  const sample = spec => spec.choices?.[0] ?? ({ number: 1, integer: 1, boolean: true,
    coordinate: 1, stringOrInteger: 'all', integerOrDefault: 'default' }[spec.type] ?? 'Alice');
  const calls = [];
  for (const [operation, variants] of Object.entries(mockOperations)) {
    for (const shape of variants) {
      const args = shape.positional.map(([, spec]) => JSON.stringify(sample(spec)));
      calls.push(`assert(bot("Alice"):${operation}(${args.join(',')}).ok)`);
    }
  }
  const run = startRun(`params{}\n${calls.join('\n')}`, {}, { onOperation: e => { received.push(e); } });
  assert.equal((await run.done).type, 'done');
  assert.equal(received.length, calls.length);
  assert.deepEqual(new Set(received.map(e => e.operation)), new Set(Object.keys(mockOperations)));
  assert.equal(received.every(e => e.target === 'Alice' && typeof e.params === 'object'), true);
});

test('param constructors, typed args, group members, acceptance failures and waitOnline', async () => {
  const events = [];
  const source = `local a=params {param.group "group",param.pos("origin","1 64 2"),param.number("spacing",2),
    param.bot "name",param.bool("flag",false),param.string("text","x")}
assert(a.origin.x==1 and a.origin.y==64 and a.flag==false and a.text=="x")
waitOnline(bot(a.name))
for i, member in group(a.group):members() do
    local result=member:tp(a.origin.x+(i-1)*a.spacing,a.origin.y,a.origin.z)
    assert(result.ok == false and result.reason == "rejected")
end
bot(a.name):drop("all")
group(a.group):destroy{mode="continuous",count=64}`;
  const run = startRun(source, { group: '#miners', name: 'Alice' }, {
    onMembers: () => 2,
    onWaitOnline: () => ({ ok: true }),
    onOperation: e => { events.push(e); return {ok:false,reason:'rejected'}; },
  });
  assert.equal((await run.done).type, 'done');
  assert.deepEqual(events.map(e => e.target), ['#miners[1]', '#miners[2]', 'Alice', '#miners']);
  assert.deepEqual(events[0].params, {x:1,y:64,z:2});
  assert.deepEqual(events[1].params, {x:3,y:64,z:2});
  assert.deepEqual(events[3].params, {mode:'continuous',count:64});
});

test('typed API rejects surplus options, bad types and command-string escape attempts before IPC', async () => {
  for (const call of ['bot("A"):move("forward",{raw="stop"})', 'bot("A"):look(true,0)',
    'bot("A"):tp("Player",{raw="stop"})', 'bot("A"):command("stop")']) {
    let invoked = false;
    const run = startRun(`params{}\n${call}`, {}, { onOperation: () => { invoked = true; } });
    assert.equal((await run.done).type, 'error');
    assert.equal(invoked, false);
  }
});

test('declarations cannot perform host work, and run interrupt budgets are cumulative across yields', async () => {
  for (const source of ['bot("A"):look(0,0)\nparams{}', 'wait(1)\nparams{}', 'log("early")\nparams{}']) {
    const run = startRun(source);
    assert.equal((await run.done).type, 'error');
    assert.equal(run.events.some(e => e.type === 'operation'), false);
  }
  const run = startRun('params{}\nfor i=1,100 do for j=1,1000 do local x=j*j end wait(0) end', {}, {
    limits: { interruptBudget: 2000, sliceMs: 1000, tickMs: 0 },
  });
  assert.match((await run.done).message, /execution budget exceeded/);
});

test('operator API reference matches the operation table shipped in the plugin', async () => {
  const guide = await readFile(new URL('../docs/SCRIPTING.md', import.meta.url), 'utf8');
  const section = guide.split('<!-- BEGIN GENERATED OPERATIONS -->\n')[1].split('<!-- END GENERATED OPERATIONS -->')[0];
  assert.equal(section, reference(mockOperations));
});

test('both menu templates compile and execute with the real generated API', async () => {
  const forms = await readFile(new URL('../plugin/endbot/src/endstone_endbot/script_forms.py', import.meta.url), 'utf8');
  for (const [title, args] of [['Group row', {group:'#miners',origin:'1 64 2'}], ['Simple patrol', {name:'Alice',ticks:1}]]) {
    const source = new RegExp(`"${title}": """([\\s\\S]*?)"""`).exec(forms)?.[1];
    assert.ok(source, `missing menu template ${title}`);
    const events = [];
    const run = startRun(source, args, { limits:{tickMs:1}, onMembers:()=>2, onOperation:e=>{events.push(e);} });
    assert.equal((await run.done).type, 'done');
    assert.equal(events.length, title === 'Group row' ? 2 : 8);
  }
});

test('native source limit is supplied by the host', async () => {
  const run = startRun('params{}\nlog("oversize")', {}, { limits:{maxSourceBytes:8} });
  const result = await run.done;
  assert.equal(result.type, 'error');
  assert.match(result.message, /source too large/);
});

test('array declarations and positional values preserve nonalphabetical order and trailing defaults', async () => {
  const source = `local args = params {param.string("zeta"),param.number("alpha",2),param.bool("flag",false)}
assert(args.zeta == "first" and args.alpha == 2 and args.flag == false)`;
  const run = startRun(source, ['first']);
  assert.equal((await run.done).type, 'done');
  assert.deepEqual(run.events[0].schema.map(spec => spec.name), ['zeta', 'alpha', 'flag']);
  const empty = startRun('params{}');
  assert.equal((await empty.done).type, 'done');
  assert.deepEqual(empty.events[0].schema, []);
});

test('declaration errors retain params call line and never reach the body', async () => {
  for (const [schema, message] of [
    ['42', 'Script parameters must be an array'],
    ['{z=param.string "z"}', 'Script parameters must be an array'],
    ['{param.string "z", extra=param.number "a"}', 'Script parameters must be an array'],
    ['{[1]=param.string "z", [3]=param.number "a"}', 'Script parameters must be an array'],
    ['{param.string "z",param.number "z"}', 'Duplicate script parameter name'],
    ['{param.string "bad name"}', 'Invalid script parameter name'],
    ['{param.string()}', 'Invalid script parameter name'],
    ['{{name="x",type="command"}}', 'Unknown script parameter type'],
    ['{param.number("x",true)}', 'Invalid script parameter default'],
    ['{param.number("x",0/0)}', 'Invalid script parameter default'],
  ]) {
    const run = startRun(`local x = 1\nlocal a = params(${schema})\nlog("wrong")`);
    const result = await run.done;
    assert.equal(result.type, 'error', schema);
    assert.equal(result.message, `script:2: ${message}`, schema);
    assert.equal(run.events.some(e => e.type === 'params' || e.type === 'log'), false);
  }
});

test('host independently validates array schemas before accepting arguments', () => {
  for (const [schema, message] of [
    [{}, /must be an array/],
    [[{name:'x',type:'number'},{name:'x',type:'string'}], /Duplicate/],
    [[{name:'bad name',type:'number'}], /Invalid script parameter name/],
    [[{name:'x',type:'command'}], /Unknown script parameter type/],
    [[{name:'x',type:'number',default:true}], /default/],
    [[{name:'x',type:'string',extra:1}], /declarations/],
  ]) assert.throws(() => parameters(schema, {}), message);
  assert.throws(() => parameters([], [1]), /Too many/);
});
