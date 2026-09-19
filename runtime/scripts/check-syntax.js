#!/usr/bin/env node
// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

// `node --check` accepts one file and cmd.exe does not expand globs, so
// check each runtime source file explicitly instead of relying on the shell.
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const runtimeRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const directories = ['src', 'scripts']
let failures = 0
for (const directory of directories) {
  const absolute = path.join(runtimeRoot, directory)
  for (const name of fs.readdirSync(absolute).filter(entry => entry.endsWith('.js')).sort()) {
    const file = path.join(absolute, name)
    const result = spawnSync(process.execPath, ['--check', file], { stdio: 'inherit' })
    if (result.error) throw result.error
    if (result.status !== 0) failures += 1
  }
}
if (failures > 0) {
  console.error(`check-syntax: ${failures} file(s) failed`)
  process.exit(1)
}
