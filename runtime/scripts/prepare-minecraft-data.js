#!/usr/bin/env node
// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const MINECRAFT_DATA_URL = 'https://github.com/amatouhake/minecraft-data.git'
export const MINECRAFT_DATA_COMMIT = '7c1fe886dd92837c0550e8eff91440361c7d677f'
export const MINECRAFT_VERSION = '1.26.50'
export const MINECRAFT_PROTOCOL = 2193
export const ENDBOT_SCHEMA_REVISION = 23

const runtimeRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const packageRoot = path.join(runtimeRoot, 'node_modules', 'minecraft-data')
const installedData = path.join(packageRoot, 'minecraft-data', 'data')
const marker = path.join(packageRoot, '.endbot-minecraft-data-revision')
const markerValue = `${MINECRAFT_DATA_COMMIT}:endbot-schema-${ENDBOT_SCHEMA_REVISION}`

export function patchPinnedProtocolSchema (protocolPath) {
  const protocol = JSON.parse(fs.readFileSync(protocolPath, 'utf8'))
  const standaloneLegacy = structuredClone(protocol.types.TransactionLegacy)
  const legacySlots = standaloneLegacy[1].find(value => value.name === 'legacy_transactions')
  legacySlots.type = ['option', legacySlots.type[1].default]
  protocol.types.TransactionLegacyStandalone = standaloneLegacy
  protocol.types.Transaction[1].find(value => value.name === 'legacy').type = 'TransactionLegacyStandalone'

  // Protocol 2193 writes the embedded action array directly. The pinned data
  // modeled that array as optional and inserted a byte that shifts ActionType.
  const authInputFields = protocol.types.packet_player_auth_input[1]
  const embeddedTransaction = authInputFields.find(value => value.name === 'transaction').type[1]
  const embeddedFields = embeddedTransaction[1]
  // Protocol 2193's packed auth-input form carries an explicit legacy-slots
  // presence byte even when the legacy request id is zero.
  embeddedFields.find(value => value.name === 'legacy').type = 'TransactionLegacyStandalone'
  embeddedFields.find(value => value.name === 'actions').type = 'TransactionActions'
  // Keep a named packed type so its protocol-2193 layout is tested separately
  // from future standalone InventoryTransaction evolution.
  const authInputUse = structuredClone(protocol.types.TransactionUseItem)
  protocol.types.TransactionUseItemAuthInput = authInputUse
  embeddedFields.find(value => value.name === 'data').type = 'TransactionUseItemAuthInput'

  fs.writeFileSync(protocolPath, `${JSON.stringify(protocol, null, 2)}\n`)
}

function run (command, commandArguments, options = {}) {
  const result = spawnSync(command, commandArguments, { stdio: 'inherit', ...options })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`${command} exited with status ${result.status}`)
}

export function prepareMinecraftData () {
  if (!fs.existsSync(path.join(packageRoot, 'package.json'))) {
    throw new Error('minecraft-data dependency is not installed')
  }
  try {
    const installedVersion = JSON.parse(fs.readFileSync(
      path.join(installedData, 'bedrock', MINECRAFT_VERSION, 'version.json'),
      'utf8'
    ))
    if (
      fs.readFileSync(marker, 'utf8').trim() === markerValue &&
      installedVersion.minecraftVersion === MINECRAFT_VERSION &&
      installedVersion.version === MINECRAFT_PROTOCOL
    ) return
  } catch (error) {
    if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error
  }

  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-minecraft-data-'))
  try {
    run('git', ['init', '--quiet'], { cwd: temporary })
    run('git', ['remote', 'add', 'origin', MINECRAFT_DATA_URL], { cwd: temporary })
    run('git', ['fetch', '--quiet', '--depth', '1', 'origin', MINECRAFT_DATA_COMMIT], { cwd: temporary })
    run('git', ['checkout', '--quiet', '--detach', 'FETCH_HEAD'], { cwd: temporary })
    const revision = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: temporary, encoding: 'utf8' })
    if (revision.status !== 0 || revision.stdout.trim() !== MINECRAFT_DATA_COMMIT) {
      throw new Error('Fetched minecraft-data revision did not match the M2 pin')
    }
    fs.rmSync(installedData, { recursive: true, force: true })
    fs.cpSync(path.join(temporary, 'data'), installedData, { recursive: true })
    // Node refuses to spawn a .cmd shim without a shell on Windows
    // (CVE-2024-27980), so the npm launcher needs cmd.exe there. The
    // arguments are fixed literals, so no shell quoting is involved.
    const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
    run(npm, ['run', 'generate:data'], { cwd: packageRoot, shell: process.platform === 'win32' })
    patchPinnedProtocolSchema(path.join(installedData, 'bedrock', MINECRAFT_VERSION, 'protocol.json'))
    const preparedVersion = JSON.parse(fs.readFileSync(
      path.join(installedData, 'bedrock', MINECRAFT_VERSION, 'version.json'),
      'utf8'
    ))
    if (
      preparedVersion.minecraftVersion !== MINECRAFT_VERSION ||
      preparedVersion.version !== MINECRAFT_PROTOCOL
    ) throw new Error('Prepared minecraft-data revision does not contain the pinned BDS protocol schema')
    fs.writeFileSync(marker, `${markerValue}\n`)
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true })
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) prepareMinecraftData()
