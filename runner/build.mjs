import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('.', import.meta.url));
const lock = JSON.parse(await readFile(path.join(root, 'luau.lock.json'), 'utf8'));
const deps = path.join(root, '.deps');
await mkdir(deps, { recursive: true });

function command(exe, args) {
  const result = spawnSync(exe, args, { cwd: root, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${exe} exited ${result.status}`);
}

async function download(entry, filename) {
  const dest = path.join(deps, filename);
  let bytes;
  try { bytes = await readFile(dest); } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    const response = await fetch(entry.url, { signal: AbortSignal.timeout(60_000) });
    if (!response.ok) throw new Error(`download failed: ${response.status} ${entry.url}`);
    bytes = Buffer.from(await response.arrayBuffer());
  }
  const actual = createHash('sha256').update(bytes).digest('hex');
  if (actual !== entry.sha256) throw new Error(`checksum mismatch for ${filename}: ${actual}`);
  await writeFile(dest, bytes);
  return dest;
}

const archive = await download(lock.luau, 'luau.tar.gz');
await download(lock.json, 'json.hpp');
const source = path.join(deps, `luau-${lock.luau.commit}`);
// Restore upstream files from the verified archive even on a cached build.
command('tar', ['-xzf', path.relative(root, archive), '-C', path.relative(root, deps)]);
const cmake = process.env.CMAKE || 'cmake';
command(cmake, ['-S', '.', '-B', '.build/native', ...process.argv.slice(2), '-DCMAKE_BUILD_TYPE=Release',
  '-DLUAU_BUILD_CLI=OFF', '-DLUAU_BUILD_TESTS=OFF', '-DLUAU_STATIC_CRT=ON',
  `-DLUAU_SOURCE_DIR=${source}`]);
command(cmake, ['--build', '.build/native', '--config', 'Release', '--target', 'endbot-luau-spike', '--parallel', '2']);
