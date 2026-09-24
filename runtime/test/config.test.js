// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { loadConfig } from '../src/config.js'

function writeConfig (value) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-config-'))
  const filename = path.join(directory, 'endbot-runtime.json')
  fs.writeFileSync(filename, JSON.stringify({
    dataDirectory: './data',
    controlTokenPath: './control.token',
    ownerPrivateKeyPath: './owner-private.pem',
    ...value
  }))
  return filename
}

test('loopback server hosts load and the legacy pin path is ignored', () => {
  for (const serverHost of [undefined, '127.0.0.1', 'localhost', '::1']) {
    const config = loadConfig(writeConfig({ serverHost, serverIdentityPinPath: './bds-nethernet.pin' }))
    assert.equal(config.serverHost, serverHost ?? '127.0.0.1')
    assert.equal('serverIdentityPinPath' in config, false)
  }
})

test('non-loopback server hosts are refused at load', () => {
  for (const serverHost of ['192.0.2.10', '0.0.0.0', 'example.com']) {
    assert.throws(() => loadConfig(writeConfig({ serverHost })), /loopback/)
  }
})
