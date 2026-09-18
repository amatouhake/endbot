// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test from 'node:test'

import {
  addJumpInputFlags,
  createAttackTransaction,
  createUseTransaction,
  EMPTY_ITEM,
  hasUsableHeldItem,
  serverRotation
} from '../src/packets.js'

test('jump input flags contain each protocol enum at most once', () => {
  const inputData = []
  addJumpInputFlags(inputData, { started: true, airborne: true })
  assert.deepEqual(inputData, ['jump_down', 'start_jumping', 'jumping'])
  assert.equal(new Set(inputData).size, inputData.length)
})

test('empty-hand use is a safe no-op instead of an invalid transaction', () => {
  assert.equal(hasUsableHeldItem(EMPTY_ITEM), false)
  assert.equal(hasUsableHeldItem({ network_id: 1 }), true)
})

test('server teleport rotation can synchronize subsequent client input', () => {
  assert.deepEqual(serverRotation({ yaw: 123, pitch: 10 }), { yaw: 123, pitch: 10 })
  assert.equal(serverRotation({ yaw: Number.NaN, pitch: 10 }), undefined)
})

const require = createRequire(import.meta.url)
const { createDeserializer, createSerializer } = require('bedrock-protocol/src/transforms/serializer')
const serializer = createSerializer('1.26.50')
const deserializer = createDeserializer('1.26.50')

test('M2 use transaction serializes with the pinned 1.26.50 schema', () => {
  const transaction = createUseTransaction({ hotbarSlot: 0, heldItem: EMPTY_ITEM, position: { x: 1, y: 64, z: 2 } })
  const packet = {
    pitch: 0, yaw: 0, position: { x: 1, y: 64, z: 2 }, move_vector: { x: 0, z: 0 }, head_yaw: 0,
    input_data: ['perform_item_interaction'], input_mode: 'mouse', play_mode: 'normal',
    interaction_model: 'crosshair', interact_rotation: { x: 0, z: 0 }, tick: 1n,
    delta: { x: 0, y: 0, z: 0 }, transaction, item_stack_request: undefined, block_action: undefined,
    vehicle_rotation: undefined, predicted_vehicle: undefined, analogue_move_vector: { x: 0, z: 0 },
    camera_orientation: { x: 0, y: 0, z: 1 }, raw_move_vector: { x: 0, z: 0 }
  }
  const wire = serializer.createPacketBuffer({ name: 'player_auth_input', params: packet })
  const decoded = deserializer.parsePacketBuffer(wire).data
  assert.deepEqual(decoded.params.input_data, ['perform_item_interaction'])
  assert.equal(decoded.params.transaction.data.action_type, 'click_air')
})

test('M2 entity attack transaction serializes with the pinned 1.26.50 schema', () => {
  const packet = createAttackTransaction({
    runtimeId: 7n,
    hotbarSlot: 0,
    heldItem: EMPTY_ITEM,
    position: { x: 1, y: 64, z: 2 }
  })
  const wire = serializer.createPacketBuffer({ name: 'inventory_transaction', params: packet })
  const decoded = deserializer.parsePacketBuffer(wire).data
  assert.equal(decoded.params.transaction.transaction_data.action_type, 'attack')
})
