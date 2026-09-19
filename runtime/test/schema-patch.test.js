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
      Transaction: ['container', [{ name: 'legacy', type: 'TransactionLegacy' }]],
      TransactionUseItem: ['container', [
        { name: 'hotbar_slot', type: 'zigzag32' },
        { name: 'hand', type: 'u8' },
        { name: 'held_item', type: 'ItemV4' }
      ]],
      Action: ['mapper', { type: 'zigzag32', mappings: { 28: 'start_item_use_on' } }],
      InputData: ['mapper', { type: 'zigzag32', mappings: {
        2: 'north_jump',
        34: 'perform_item_interaction',
        35: 'perform_block_actions',
        53: 'start_using_item'
      } }],
      packet_player_auth_input: ['container', [
        { name: 'transaction', type: ['option', ['container', [
          { name: 'legacy', type: 'TransactionLegacy' },
          { name: 'actions', type: ['option', 'TransactionActions'] },
          { name: 'data', type: 'TransactionUseItem' }
        ]]] },
        { name: 'item_stack_request', type: ['option', 'StackRequest'] },
        { name: 'block_action', type: ['option', ['array', {
          countType: 'varint',
          type: ['container', [
            { name: 'action', type: 'Action' },
            { name: 'position', type: 'vec3i' },
            { name: 'face', type: 'zigzag32' }
          ]]
        }]] },
        { name: 'vehicle_rotation', type: ['option', 'Rotation'] },
        { name: 'predicted_vehicle', type: ['option', 'Vehicle'] }
      ]]
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
    const embeddedTransaction = patched.types.packet_player_auth_input[1]
      .find(value => value.name === 'transaction').type[1]
    assert.equal(embeddedTransaction[1].find(value => value.name === 'legacy').type, 'TransactionLegacyStandalone')
    assert.equal(embeddedTransaction[1].find(value => value.name === 'actions').type, 'TransactionActions')
    assert.equal(embeddedTransaction[1].find(value => value.name === 'data').type, 'TransactionUseItemAuthInput')
    assert.deepEqual(patched.types.TransactionUseItem[1].map(value => value.name), [
      'hotbar_slot', 'hand', 'held_item'
    ])
    assert.deepEqual(patched.types.TransactionUseItemAuthInput[1].map(value => value.name), [
      'hotbar_slot', 'hand', 'held_item'
    ])
    assert.equal(patched.types.Action[1].mappings[28], 'start_item_use_on')
    assert.equal(patched.types.InputData[1].mappings[2], 'north_jump')
    assert.equal(patched.types.InputData[1].mappings[34], 'perform_item_interaction')
    assert.equal(patched.types.InputData[1].mappings[35], 'perform_block_actions')
    assert.equal(patched.types.InputData[1].mappings[53], 'start_using_item')
  } finally {
    fs.rmSync(directory, { recursive: true, force: true })
  }
})
