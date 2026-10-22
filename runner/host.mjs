import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('.', import.meta.url));
export const executable = path.join(root, '.build', 'native', 'bin', `endbot-luau-spike${process.platform === 'win32' ? '.exe' : ''}`);
const bootstrap = readFileSync(path.join(root, 'api.luau'), 'utf8');
// Exercise the production grammar; this standalone host still never controls BDS.
export const mockOperations = JSON.parse(readFileSync(new URL('../plugin/endbot/src/endstone_endbot/operations.json', import.meta.url), 'utf8'));
const defaults = Object.freeze({ sliceMs: 100, interruptBudget: 100000, memoryBytes: 32 * 1024 * 1024,
  wallMs: 5000, requestMs: 1000, maxMessages: 1000, maxTicks: 2000, tickMs: 50 });

export function parameters(schema, args) {
  if (!Array.isArray(schema)) throw new Error('Script parameters must be an array');
  if (schema.length > 32) throw new Error('Invalid script parameter declarations');
  const names = new Set();
  for (const spec of schema) {
    if (!spec || typeof spec !== 'object' || Array.isArray(spec)
      || Object.keys(spec).some(key => !['name', 'type', 'default'].includes(key))) throw new Error('Invalid script parameter declarations');
    if (typeof spec.name !== 'string' || !/^[A-Za-z_][A-Za-z0-9_]{0,31}$/.test(spec.name)) throw new Error('Invalid script parameter name');
    if (names.has(spec.name)) throw new Error('Duplicate script parameter name');
    names.add(spec.name);
    if (!['string', 'number', 'boolean', 'bot', 'group', 'pos'].includes(spec.type)) throw new Error('Unknown script parameter type');
    const expected = ['number', 'boolean'].includes(spec.type) ? spec.type : 'string';
    if (Object.hasOwn(spec, 'default') && (typeof spec.default !== expected
      || (expected === 'number' && !Number.isFinite(spec.default)))) throw new Error('Invalid script parameter default');
  }
  if (Array.isArray(args)) {
    if (args.length > schema.length) throw new Error('Too many script arguments');
    args = Object.fromEntries(args.map((value, index) => [schema[index].name, value]));
  }
  const result = {};
  for (const key of Object.keys(args)) if (!names.has(key)) throw new Error(`unknown argument: ${key}`);
  for (const descriptor of schema) {
    const key = descriptor.name;
    let value = Object.hasOwn(args, key) ? args[key] : descriptor.default;
    if (descriptor.type === 'pos') {
      const coordinates = typeof value === 'string' ? value.split(/\s+/).map(Number) : [];
      if (coordinates.length !== 3 || coordinates.some(v => !Number.isFinite(v))) throw new Error(`invalid argument: ${key}`);
      value = Object.fromEntries(['x', 'y', 'z'].map((axis, i) => [axis, coordinates[i]]));
    } else if (typeof value !== (['bot', 'group'].includes(descriptor.type) ? 'string' : descriptor.type)
      || (typeof value === 'number' && !Number.isFinite(value))) throw new Error(`invalid argument: ${key}`);
    Object.defineProperty(result, key, { value, enumerable: true });
  }
  return result;
}

// Test harness only. Its default authorizer permits synthetic requests and never talks to BDS.
export function startRun(source, args = {}, options = {}) {
  const limits = { ...defaults, ...options.limits };
  for (const [key, value] of Object.entries(limits)) {
    if (!Number.isFinite(value) || value < 0 || (key !== 'tickMs' && (!Number.isSafeInteger(value) || value < 1))) throw new Error(`invalid limit: ${key}`);
  }
  if (typeof source !== 'string' || Buffer.byteLength(source) > 65536) throw new Error('source too large or not text');
  if (!args || typeof args !== 'object') throw new Error('args must be a JSON object or array');
  const load = JSON.stringify({ type: 'load', source, bootstrap, limits, operations: options.operations ?? mockOperations });
  if (Buffer.byteLength(load) > 256 * 1024) throw new Error('load frame too large');
  const child = spawn(executable, [], { cwd: root, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  const events = [];
  let buffer = '', terminal, closed = false, stopping = false, declared = false, started = false, count = 0, expectedId = 1;
  let resolveDone, resolveReady;
  const done = new Promise(resolve => { resolveDone = resolve; });
  const ready = new Promise(resolve => { resolveReady = resolve; });
  const timers = new Set();
  const timer = (callback, ms) => {
    const handle = setTimeout(() => { timers.delete(handle); callback(); }, ms);
    timers.add(handle);
    return handle;
  };
  const clear = handle => { clearTimeout(handle); timers.delete(handle); };
  function finish(event) {
    if (terminal) return;
    terminal = event;
    events.push(event);
    for (const handle of timers) clearTimeout(handle);
    timers.clear();
    resolveReady(event);
    child.stdin.end();
  }
  function kill(message, type = 'error') {
    finish({ type, phase: 'host', message });
    if (!closed) child.kill();
  }
  function send(message) {
    if (terminal || closed || (stopping && message.type !== 'stop')) return;
    const line = JSON.stringify(message);
    if (Buffer.byteLength(line) > 256 * 1024) return kill('reply too large');
    child.stdin.write(line + '\n');
  }
  function stop() {
    if (terminal || stopping) return;
    stopping = true;
    // Cooperative while yielded; forced termination also covers CPU-bound code and blocked host handlers.
    send({ type: 'stop', id: 'host-stop' });
    timer(() => kill('host stop fallback', 'stopped'), 100);
  }
  function begin() {
    if (started || terminal || stopping || !declared) return;
    started = true;
    try { send({ type: 'reply', id: 1, accepted: true, args: parameters(events.find(e => e.type === 'params').schema, args) }); }
    catch (error) { send({ type: 'reply', id: 1, accepted: false, error: error.message }); }
  }
  async function handle(event) {
    if (terminal) return;
    if (++count > limits.maxMessages) return kill('message budget exceeded');
    if (['done', 'error', 'stopped'].includes(event.type)) {
      if (event.type === 'done' && !declared) return kill('script must declare params{}');
      finish(event);
      return;
    }
    if (stopping) return;
    events.push(event);
    if (event.type === 'log') return;
    if (event.id !== expectedId++) return kill('unexpected runner request id');
    if (event.type === 'params') {
      if (declared || event.id !== 1) return kill('params must be first request');
      declared = true;
      resolveReady(event);
      if (!options.pauseBeforeStart) begin();
      return;
    }
    if (!started) return kill('operation before params acceptance');
    const waitMs = event.type === 'wait' && Number.isSafeInteger(event.ticks) && event.ticks >= 0 && event.ticks <= limits.maxTicks
      ? event.ticks * limits.tickMs : 0;
    const timeout = timer(() => kill('host request timeout'), limits.requestMs + waitMs);
    try {
      if (event.type === 'wait') {
        if (!Number.isSafeInteger(event.ticks) || event.ticks < 0 || event.ticks > limits.maxTicks) throw new Error('invalid wait ticks');
        if (options.onWait) await options.onWait(event);
        else await new Promise(resolve => timer(resolve, event.ticks * limits.tickMs));
      } else if (event.type === 'operation') {
        if (typeof event.target !== 'string' || !event.target.length || event.target.length > 64) throw new Error('invalid target');
        if (!Object.hasOwn(options.operations ?? mockOperations, event.operation)) throw new Error('unsupported operation');
        if (!event.params || typeof event.params !== 'object' || Array.isArray(event.params)) throw new Error('invalid params');
        // Re-evaluate authority on every operation, including those after a wait.
        if (options.authorize && !await options.authorize(event)) throw new Error('authorization revoked');
        if (terminal || stopping) return;
        const result = await options.onOperation?.(event) ?? { ok: true };
        send({ type: 'reply', id: event.id, accepted: true, result });
        return;
      } else if (event.type === 'members') {
        send({ type: 'reply', id: event.id, accepted: true, count: await options.onMembers?.(event) ?? 0 });
        return;
      } else if (event.type === 'waitOnline') {
        send({ type: 'reply', id: event.id, accepted: true, result: await options.onWaitOnline?.(event) ?? { ok: true } });
        return;
      } else throw new Error('unknown request');
      send({ type: 'reply', id: event.id, accepted: true });
    } catch (error) {
      send({ type: 'reply', id: event.id, accepted: false, error: error.message });
    } finally { clear(timeout); }
  }
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', chunk => {
    if (terminal) return;
    buffer += chunk;
    let newline;
    while ((newline = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, newline); buffer = buffer.slice(newline + 1);
      if (Buffer.byteLength(line) > 256 * 1024) { kill('runner frame too large'); return; }
      try { void handle(JSON.parse(line)).catch(error => kill(error.message)); }
      catch { kill('malformed runner JSON'); return; }
      if (terminal) return;
    }
    if (Buffer.byteLength(buffer) > 256 * 1024) kill('runner frame too large');
  });
  // Stderr is drained but bounded; protocol messages are exclusively stdout.
  let stderrBytes = 0;
  child.stderr.on('data', chunk => { stderrBytes += chunk.length; if (stderrBytes > 65536) kill('runner stderr too large'); });
  child.on('error', error => { finish({ type: 'error', phase: 'spawn', message: error.message }); });
  child.stdin.on('error', error => { if (!terminal) kill(`runner stdin: ${error.message}`); });
  child.on('close', (code, signal) => {
    closed = true;
    if (!terminal) finish({ type: 'error', phase: 'exit', message: `runner exited ${code}/${signal}` });
    resolveDone({ ...terminal, exitCode: code, signal });
  });
  timer(() => kill('wall time budget exceeded'), limits.wallMs);
  send(JSON.parse(load));
  return { ready, done, events, begin, stop };
}
