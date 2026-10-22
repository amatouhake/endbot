import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile, copyFile, rm } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('.', import.meta.url));
// Resolve staging paths from the caller's cwd, even when invoked from the repo root.
const args = process.argv.slice(2);
let out = process.env.ENDBOT_RUNNER_OUT;
const cmakeArgs = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--out') {
    if (!args[i + 1] || args[i + 1].startsWith('-')) throw new Error('--out requires a directory');
    out = args[++i];
  } else cmakeArgs.push(args[i]);
}
const bin = path.join(root, '.build/native/bin');
const stage = out ? path.resolve(out) : bin;
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
if (!path.resolve(source).startsWith(path.resolve(deps) + path.sep)) throw new Error('source must stay inside .deps');
await rm(source, { recursive: true, force: true });
command('tar', ['-xzf', path.relative(root, archive), '-C', path.relative(root, deps)]);
const cmake = process.env.CMAKE || 'cmake';
// Ninja works with MSVC's developer environment and with Linux GCC/Clang.
// An explicit -G remains available for local toolchains; don't reuse a build tree across generators.
const generator = cmakeArgs.some(arg => arg === '-G' || arg.startsWith('-G')) ? [] : ['-G', 'Ninja'];
command(cmake, ['-S', '.', '-B', '.build/native', ...generator, ...cmakeArgs, '-DCMAKE_BUILD_TYPE=Release',
  '-DLUAU_BUILD_CLI=OFF', '-DLUAU_BUILD_TESTS=OFF', '-DLUAU_STATIC_CRT=ON',
  `-DLUAU_SOURCE_DIR=${source}`]);
command(cmake, ['--build', '.build/native', '--config', 'Release', '--target', 'endbot-luau-runner', '--parallel', '2']);
// Install the trusted sidecar beside the binary; plugin bundles use this layout too.
await copyFile(path.join(root, 'api.luau'), path.join(bin, 'api.luau'));
await mkdir(stage, { recursive: true });
if (stage !== bin) {
  const executable = `endbot-luau-runner${process.platform === 'win32' ? '.exe' : ''}`;
  await copyFile(path.join(bin, executable), path.join(stage, executable));
  await copyFile(path.join(bin, 'api.luau'), path.join(stage, 'api.luau'));
}
// Only the two lock-file URLs are fetched. Luau's license comes from its verified archive;
// json.hpp carries its SPDX notice and the complete MIT grant is retained locally.
await copyFile(path.join(source, 'LICENSE.txt'), path.join(stage, 'LUAU_LICENSE.txt'));
await copyFile(path.join(root, 'licenses/NLOHMANN_JSON_LICENSE.txt'), path.join(stage, 'NLOHMANN_JSON_LICENSE.txt'));
