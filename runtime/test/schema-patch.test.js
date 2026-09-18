// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { patchPinnedProtocolSchema } from '../scripts/prepare-minecraft-data.js'

test('pinned schema separates standalone and PlayerAuthInput legacy layouts', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-schema-test-'))
  const protocolPath = path.join(directory, 'protocol.json')
  const conditional = ['switch', {
    compareTo: 'legacy_request_id',
    fields: { 0: 'void' },
    default: ['array', { countType: 'varint', type: 'u8' }]
  }]
  const protocol = {
    types: {
      TransactionLegacy: ['container', [
        { name: 'legacy_request_id', type: 'zigzag32' },
        { name: 'legacy_transactions', type: conditional }
      ]],
      Transaction: ['container', [{ name: 'legacy', type: 'TransactionLegacy' }]]
    }
  }

  try {
    fs.writeFileSync(protocolPath, JSON.stringify(protocol))
    patchPinnedProtocolSchema(protocolPath)
    const patched = JSON.parse(fs.readFileSync(protocolPath, 'utf8'))

    assert.deepEqual(patched.types.TransactionLegacy[1][1].type, conditional)
    assert.deepEqual(patched.types.TransactionLegacyStandalone[1][1].type, [
      'option',
      conditional[1].default
    ])
    assert.equal(patched.types.Transaction[1][0].type, 'TransactionLegacyStandalone')
  } finally {
    fs.rmSync(directory, { recursive: true, force: true })
  }
})
