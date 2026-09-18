// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test from 'node:test'

import { createAttackTransaction, createUseTransaction, EMPTY_ITEM } from '../src/packets.js'

const require = createRequire(import.meta.url)
const { createProtocol } = require('bedrock-protocol/src/transforms/serializer')
const protocol = createProtocol('1.26.50')

test('M2 use transaction serializes with the pinned 1.26.50 schema', () => {
  const transaction = createUseTransaction({ hotbarSlot: 0, heldItem: EMPTY_ITEM, position: { x: 1, y: 64, z: 2 } })
  const packet = {
    pitch: 0, yaw: 0, position: { x: 1, y: 64, z: 2 }, move_vector: { x: 0, z: 0 }, head_yaw: 0,
    input_data: ['item_interact', 'start_using_item'], input_mode: 'mouse', play_mode: 'normal',
    interaction_model: 'crosshair', interact_rotation: { x: 0, z: 0 }, tick: 1n,
    delta: { x: 0, y: 0, z: 0 }, transaction, item_stack_request: undefined, block_action: undefined,
    vehicle_rotation: undefined, predicted_vehicle: undefined, analogue_move_vector: { x: 0, z: 0 },
    camera_orientation: { x: 0, y: 0, z: 1 }, raw_move_vector: { x: 0, z: 0 }
  }
  assert.doesNotThrow(() => protocol.createPacketBuffer('packet_player_auth_input', packet))
})

test('M2 entity attack transaction serializes with the pinned 1.26.50 schema', () => {
  const packet = createAttackTransaction({
    runtimeId: 7n,
    hotbarSlot: 0,
    heldItem: EMPTY_ITEM,
    position: { x: 1, y: 64, z: 2 }
  })
  assert.doesNotThrow(() => protocol.createPacketBuffer('packet_inventory_transaction', packet))
})
