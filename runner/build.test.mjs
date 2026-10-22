import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, copyFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { executable } from './host.mjs';

test('product executable uses the runner name', () => {
  assert.equal(path.basename(executable), `endbot-luau-runner${process.platform === 'win32' ? '.exe' : ''}`);
});

test('--out requires a directory before acquiring dependencies', () => {
  const result = spawnSync(process.execPath, [fileURLToPath(new URL('./build.mjs', import.meta.url)), '--out'],
    { encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /--out requires a directory/);
});

test('a corrupt cached archive fails checksum verification before extraction or CMake', async t => {
  const fixture = await mkdtemp(path.join(tmpdir(), 'endbot-runner-build-'));
  t.after(() => rm(fixture, { recursive: true, force: true }));
  await copyFile(new URL('./build.mjs', import.meta.url), path.join(fixture, 'build.mjs'));
  await copyFile(new URL('./luau.lock.json', import.meta.url), path.join(fixture, 'luau.lock.json'));
  await mkdir(path.join(fixture, '.deps'));
  await writeFile(path.join(fixture, '.deps/luau.tar.gz'), 'corrupt-cache');
  const result = spawnSync(process.execPath, [path.join(fixture, 'build.mjs'), '--out', 'stage with spaces'],
    { cwd: fixture, encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /checksum mismatch for luau.tar.gz/);
  assert.doesNotMatch(result.stderr, /spawnSync|download failed/);
});

test('a corrupt cached JSON header also fails before extraction or CMake', async t => {
  const fixture = await mkdtemp(path.join(tmpdir(), 'endbot-runner-build-'));
  t.after(() => rm(fixture, { recursive: true, force: true }));
  await copyFile(new URL('./build.mjs', import.meta.url), path.join(fixture, 'build.mjs'));
  // A fixture archive with its own valid checksum lets the second verification run without network.
  const archive = Buffer.from('fixture archive');
  await writeFile(path.join(fixture, 'luau.lock.json'), JSON.stringify({
    luau: { sha256: createHash('sha256').update(archive).digest('hex') },
    json: { sha256: '0'.repeat(64) },
  }));
  await mkdir(path.join(fixture, '.deps'));
  await writeFile(path.join(fixture, '.deps/luau.tar.gz'), archive);
  await writeFile(path.join(fixture, '.deps/json.hpp'), 'corrupt header');
  const result = spawnSync(process.execPath, [path.join(fixture, 'build.mjs')], { encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /checksum mismatch for json.hpp/);
  assert.doesNotMatch(result.stderr, /spawnSync|download failed/);
});
