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
  const run = startRun('params{}\nwhile true do end', {}, { limits: { interruptBudget: 100000000, sliceMs: 10 } });
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

test('positions are vectors that operations expand, and choices are validated', async () => {
  const events = [];
  const source = `local a=params {param.pos("origin","1 64 2"),param.choice("mode",{"once","continuous"},"once")}
assert(type(a.origin)=="vector" and a.origin.x==1 and a.mode=="continuous")
bot("Alice"):tp(a.origin + vector.create(2, 0, 0) * 3)
bot("Alice"):tp(a.origin, {yaw=90, pitch=0})`;
  const run = startRun(source, { mode: 'continuous' }, { onOperation: e => { events.push(e); return { ok: true }; } });
  assert.equal((await run.done).type, 'done');
  assert.deepEqual(events.map(e => e.params), [{x:7,y:64,z:2}, {x:1,y:64,z:2,yaw:90,pitch:0}]);
  assert.equal(run.events[0].schema[1].type, 'choice');
  assert.deepEqual(run.events[0].schema[1].choices, ['once', 'continuous']);
  assert.throws(() => parameters(run.events[0].schema, { mode: 'twice' }), /invalid argument: mode/);
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

test('declarations cannot perform host work, and interrupt budgets reset across yields', async () => {
  for (const source of ['bot("A"):look(0,0)\nparams{}', 'wait(1)\nparams{}', 'log("early")\nparams{}']) {
    const run = startRun(source);
    assert.equal((await run.done).type, 'error');
    assert.equal(run.events.some(e => e.type === 'operation'), false);
  }
  const run = startRun('params{}\nfor i=1,100 do for j=1,1000 do local x=j*j end wait(0) end', {}, {
    limits: { interruptBudget: 2000, sliceMs: 1000, tickMs: 0 },
  });
  assert.equal((await run.done).type, 'done');
});

test('operator API reference matches the operation table shipped in the plugin', async () => {
  // Windows CI checks files out with CRLF line endings.
  const guide = (await readFile(new URL('../docs/SCRIPTING.md', import.meta.url), 'utf8')).replace(/\r\n/g, '\n');
  const section = guide.split('<!-- BEGIN GENERATED OPERATIONS -->\n')[1].split('<!-- END GENERATED OPERATIONS -->')[0];
  assert.equal(section, reference(mockOperations));
});

test('all menu templates compile and execute with the real generated API', async () => {
  const forms = await readFile(new URL('../plugin/endbot/src/endstone_endbot/script_forms.py', import.meta.url), 'utf8');
  for (const [title, args] of [['Group row', {group:'#miners',origin:'1 64 2'}], ['Simple patrol', {name:'Alice',ticks:1}],
    ['Mine at spots', {name:'Alice',start:'1 64 2',spots:2,step:4,blocks:2}]]) {
    const source = new RegExp(`"${title}": """([\\s\\S]*?)"""`).exec(forms)?.[1];
    assert.ok(source, `missing menu template ${title}`);
    const events = [];
    const run = startRun(source, args, { limits:{tickMs:1}, onMembers:()=>2, onOperation:e=>{events.push(e);},
      onWaitMining:()=>({ok:true,reason:'block count reached',results:[]}) });
    assert.equal((await run.done).type, 'done');
    assert.equal(events.length, title === 'Group row' ? 2 : title === 'Mine at spots' ? 4 : 8);
    if (title === 'Mine at spots') {
      assert.deepEqual(events.filter(e=>e.operation==='tp').map(e=>e.params.x), [1,5]);
      assert.deepEqual(events.filter(e=>e.operation==='destroy').map(e=>e.params),
        [{mode:'continuous',count:2},{mode:'continuous',count:2}]);
      assert.deepEqual(run.events.filter(e=>e.type==='log').map(e=>e.text),
        ['spot 1: block count reached','spot 2: block count reached']);
    }
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
    ['{param.choice("m",{})}', 'Invalid script parameter declarations'],
    ['{param.choice("m",{"a","a"})}', 'Invalid script parameter declarations'],
    ['{param.choice("m",{"a",2})}', 'Invalid script parameter declarations'],
    ['{param.choice("m",{"a"},"b")}', 'Invalid script parameter default'],
    ['{{name="x",type="number",choices={"a"}}}', 'Invalid script parameter declarations'],
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

test('structured status and mining waits pass readable per-member data through the native bridge', async () => {
  const waits = [];
  const mining = {active:false,mode:'continuous',state:'completed',blocksBroken:2,blockCount:2,reason:'block count reached'};
  const status = {name:'Alice',identityId:'uuid',connectionState:'online',mining};
  const run = startRun(`params{}
local s = bot("Alice"):status()
assert(s.data.mining.blocksBroken == 2 and not s.data.mining.active)
local g = group("#miners"):status()
assert(g.data.results[1].data.mining.blockCount == 2)
local r = bot("Alice"):waitMining{timeout=40}
assert(r.ok and r.results[1].mining.mode == "continuous")
log(r.reason)
r = group("#miners"):waitMining()
assert(not r.ok and r.reason == "timeout")
assert(r.results[2].reason == "offline")`, {}, {
    onOperation: e => ({ok:true,data:e.target.startsWith('#') ? {results:[{ok:true,data:status}]} : status}),
    onWaitMining: e => {
      waits.push(e);
      return e.target === 'Alice' ? {ok:true,reason:'block count reached',results:[{...status,ok:true,reason:mining.reason}]}
        : {ok:false,reason:'timeout',results:[{ok:false,reason:'timeout'},{ok:false,reason:'offline'}]};
    },
  });
  assert.equal((await run.done).type, 'done');
  assert.deepEqual(waits.map(e=>[e.target,e.timeout]), [['Alice',40],['#miners',1200]]);
  assert.equal(run.events.find(e=>e.type==='log').text, 'block count reached');
});

test('waitMining rejects invalid timeouts and options before IPC', async () => {
  for (const argument of ['{timeout=-1}', '{timeout=1.5}', '{timeout=true}', '{timeout=math.huge}', '{timeout=2147483648}', '{ticks=1}', '1', 'false']) {
    const run = startRun(`params{}\nbot("A"):waitMining(${argument})`);
    assert.equal((await run.done).type, 'error');
    assert.equal(run.events.some(e=>e.type==='waitMining'), false);
  }
});

test('a continuing operation returns a job whose wait reaches the host with its timeout', async () => {
  const waits = [];
  const run = startRun(`params{}
local r = bot("Alice"):move("forward", {ticks = 40})
assert(r.ok and r.job)
local w = r.job:wait{timeout = 60}
assert(w.ok and w.code == "ok" and w.reason == "duration elapsed")
w = group("#miners"):jump({mode = "continuous"}).job:wait()
assert(not w.ok and w.code == "timeout")
assert(bot("Alice"):status().job == nil)
assert(not pcall(function() r.job:wait{ticks = 1} end))
assert(not pcall(function() r.job:wait{timeout = -1} end))
local online = bot("Alice"):waitOnline{timeout = 5}
assert(not online.ok and online.code == "timeout")
log("done")`, {}, {
    onOperation: e => e.operation === 'status' ? {ok:true,data:{name:'Alice'}}
      : {ok:true,code:'ok',job:{kind:e.operation,ids:{'uuid':7}},data:{}},
    onWaitJob: e => {
      waits.push(e);
      return e.target === 'Alice' ? {ok:true,code:'ok',reason:'duration elapsed',results:[]}
        : {ok:false,code:'timeout',reason:'timeout',results:[]};
    },
    onWaitOnline: e => { waits.push(e); return {ok:false,code:'timeout',reason:'timeout'}; },
  });
  assert.equal((await run.done).type, 'done');
  assert.deepEqual(waits.map(e=>[e.type,e.target,e.timeout,e.job?.kind]),
    [['waitJob','Alice',60,'move'],['waitJob','#miners',1200,'jump'],['waitOnline','Alice',5,undefined]]);
  assert.deepEqual(waits[0].job.ids, {uuid:7});
});

test('waitMining suspends until reply and host stop prevents later work', async () => {
  let run;
  run = startRun('params{}\nbot("A"):waitMining{timeout=20}\nlog("wrong")', {}, {
    onWaitMining: () => { run.stop(); return new Promise(()=>{}); },
  });
  assert.equal((await run.done).type, 'stopped');
  assert.equal(run.events.some(e=>e.type==='log'), false);
});

test('zero wall lifetime permits waits, positive lifetime still terminates them, negatives reject', async () => {
  const unlimited = startRun('params{}\nwait(3)\nlog("finished")', {}, {limits:{wallMs:0,tickMs:20}});
  assert.equal((await unlimited.done).type, 'done');
  const limited = startRun('params{}\nwait(1000)', {}, {limits:{wallMs:100}});
  assert.match((await limited.done).message, /wall time budget/);
  assert.throws(()=>startRun('params{}', {}, {limits:{wallMs:-1}}), /invalid limit/);
});

test('IPC message bound resets after one second independently of total lifetime', async () => {
  const run = startRun('params{}\nlog("first")\nwait(22)\nlog("second")', {}, {limits:{wallMs:0,maxMessages:3}});
  assert.equal((await run.done).type, 'done');
});
