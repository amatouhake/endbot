import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('.', import.meta.url));
export const executable = path.join(root, '.build', 'native', 'bin', `endbot-luau-spike${process.platform === 'win32' ? '.exe' : ''}`);
const bootstrap = readFileSync(path.join(root, 'api.luau'), 'utf8');
const defaults = Object.freeze({ sliceMs: 100, interruptBudget: 100000, memoryBytes: 32 * 1024 * 1024,
  wallMs: 5000, requestMs: 1000, maxMessages: 1000, maxTicks: 2000, tickMs: 50 });

function parameters(schema, args) {
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) throw new Error('invalid params schema');
  const result = {};
  for (const key of Object.keys(args)) if (!Object.hasOwn(schema, key)) throw new Error(`unknown argument: ${key}`);
  for (const [key, descriptor] of Object.entries(schema)) {
    if (!descriptor || !['string', 'number', 'boolean'].includes(descriptor.type)) throw new Error(`invalid parameter: ${key}`);
    const value = Object.hasOwn(args, key) ? args[key] : descriptor.default;
    if (typeof value !== descriptor.type || (typeof value === 'number' && !Number.isFinite(value))) throw new Error(`invalid argument: ${key}`);
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
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('args must be a JSON object');
  const load = JSON.stringify({ type: 'load', source, bootstrap, limits });
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
        if (typeof event.bot !== 'string' || !event.bot.length || event.bot.length > 64) throw new Error('invalid bot');
        if (event.operation === 'move') {
          if (!['forward', 'back', 'left', 'right'].includes(event.direction) || !Number.isSafeInteger(event.ticks) || event.ticks < 1 || event.ticks > limits.maxTicks) throw new Error('invalid move');
        } else if (event.operation === 'look') {
          if (!Number.isFinite(event.yaw) || !Number.isFinite(event.pitch) || event.pitch < -90 || event.pitch > 90) throw new Error('invalid look');
        } else throw new Error('unsupported operation');
        // Re-evaluate authority on every operation, including those after a wait.
        if (options.authorize && !await options.authorize(event)) throw new Error('authorization revoked');
        if (terminal || stopping) return;
        await options.onOperation?.(event);
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
