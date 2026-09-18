// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test from 'node:test'

import {
  addJumpInputFlags,
  createAttackTransaction,
  createEntityMouseOver,
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
  const heldItem = {
    network_id: 882,
    count: 1,
    metadata: 0,
    has_stack_id: true,
    stack_id: 34,
    block_runtime_id: 0,
    extra: { has_nbt: 0, nbt: undefined, can_place_on: [], can_destroy: [] }
  }
  const packet = createUseTransaction({ hotbarSlot: 0, heldItem, position: { x: 1, y: 64, z: 2 } })
  const wire = serializer.createPacketBuffer({ name: 'inventory_transaction', params: packet })
  assert.deepEqual([...wire.subarray(0, 5)], [0x1e, 0, 0, 2, 0])
  const decoded = deserializer.parsePacketBuffer(wire).data
  assert.equal(decoded.params.transaction.transaction_data.action_type, 'click_air')
  assert.equal(decoded.params.transaction.transaction_data.held_item.stack_id, 34)
})

test('M2 entity attack transaction serializes with the pinned 1.26.50 schema', () => {
  const packet = createAttackTransaction({
    runtimeId: 7n,
    hotbarSlot: 0,
    heldItem: EMPTY_ITEM,
    position: { x: 1, y: 64, z: 2 }
  })
  const wire = serializer.createPacketBuffer({ name: 'inventory_transaction', params: packet })
  assert.deepEqual([...wire.subarray(0, 7)], [0x1e, 0, 0, 3, 0, 7, 2])
  const decoded = deserializer.parsePacketBuffer(wire).data
  assert.equal(decoded.params.transaction.transaction_data.entity_runtime_id, 7n)
  assert.equal(decoded.params.transaction.transaction_data.action_type, 'attack')

  const mouseOver = serializer.createPacketBuffer({ name: 'interact', params: createEntityMouseOver(7n) })
  assert.equal(deserializer.parsePacketBuffer(mouseOver).data.params.target_entity_id, 7n)
})
