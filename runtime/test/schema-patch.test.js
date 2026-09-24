// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import test from 'node:test'

const require = createRequire(import.meta.url)
const protocolPath = path.join(
  path.dirname(require.resolve('minecraft-data/package.json')),
  'minecraft-data', 'data', 'bedrock', '1.26.51', 'protocol.json'
)

test('upstream schema already encodes the proven protocol-2193 layouts', () => {
  const protocol = JSON.parse(fs.readFileSync(protocolPath, 'utf8'))

  // Standalone inventory transactions carry an explicit legacy-slots
  // presence byte even when the legacy request id is zero. Upstream models
  // that array as optional inline, which is exactly the wire layout the
  // deleted Endbot schema patch produced, so no patch is needed.
  const legacySlots = protocol.types.TransactionLegacy[1]
    .find(value => value.name === 'legacy_transactions')
  assert.equal(legacySlots.type[0], 'option')
  assert.equal(legacySlots.type[1][0], 'array')
  assert.equal(protocol.types.Transaction[1].find(value => value.name === 'legacy').type, 'TransactionLegacy')
  assert.equal(protocol.types.TransactionLegacyStandalone, undefined)

  // Protocol 2193 writes the embedded PlayerAuthInput action array directly
  // with an explicit legacy-slots presence byte.
  const embeddedTransaction = protocol.types.packet_player_auth_input[1]
    .find(value => value.name === 'transaction').type[1]
  assert.equal(embeddedTransaction[0], 'container')
  const embeddedFields = embeddedTransaction[1]
  assert.equal(embeddedFields.find(value => value.name === 'legacy').type, 'TransactionLegacy')
  assert.equal(embeddedFields.find(value => value.name === 'actions').type, 'TransactionActions')
  assert.equal(embeddedFields.find(value => value.name === 'data').type, 'TransactionUseItem')

  // The packed use-item form keeps every field the block-interaction tick sends.
  assert.deepEqual(protocol.types.TransactionUseItem[1].map(value => value.name), [
    'action_type', 'trigger_type', 'block_position', 'face', 'hotbar_slot', 'hand',
    'held_item', 'player_pos', 'click_pos', 'block_runtime_id', 'client_prediction',
    'client_cooldown_state'
  ])

  // Proven enum ordinals are preserved. Upstream renamed entries 34/35
  // (perform_item_interaction -> item_interact,
  // perform_block_actions -> block_action); the ordinals — and therefore the
  // wire bytes — are unchanged.
  assert.equal(protocol.types.Action[1].mappings[28], 'start_item_use_on')
  assert.equal(protocol.types.InputData[1].mappings[2], 'north_jump')
  assert.equal(protocol.types.InputData[1].mappings[34], 'item_interact')
  assert.equal(protocol.types.InputData[1].mappings[35], 'block_action')
  assert.equal(protocol.types.InputData[1].mappings[53], 'start_using_item')
})
