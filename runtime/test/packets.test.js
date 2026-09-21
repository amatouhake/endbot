// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test from 'node:test'

import {
  addJumpInputFlags,
  addToggleInputFlags,
  createAttackTransaction,
  createBlockInteractionAction,
  createBlockInteractionInput,
  createDropTransaction,
  createEntityMouseOver,
  createReleaseTransaction,
  createUseTransaction,
  EMPTY_ITEM,
  hasUsableHeldItem,
  serverRotation
} from '../src/packets.js'
import { InputState } from '../src/actions.js'

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
  assert.deepEqual(serverRotation({ rotation: { x: -15, z: 245 } }), { yaw: 245, pitch: -15 })
  assert.equal(serverRotation({ yaw: Number.NaN, pitch: 10 }), undefined)
})

const require = createRequire(import.meta.url)
const { createDeserializer, createSerializer } = require('bedrock-protocol/src/transforms/serializer')
const serializer = createSerializer('1.26.50')
const deserializer = createDeserializer('1.26.50')

function serializeInputFlags (state) {
  const inputData = []
  addToggleInputFlags(inputData, state)
  const wire = serializer.createPacketBuffer({
    name: 'player_auth_input',
    params: {
      pitch: 0,
      yaw: 0,
      position: { x: 0, y: 64, z: 0 },
      move_vector: { x: 0, z: 0 },
      head_yaw: 0,
      input_data: inputData,
      input_mode: 'mouse',
      play_mode: 'normal',
      interaction_model: 'crosshair',
      interact_rotation: { x: 0, z: 0 },
      tick: 1n,
      delta: { x: 0, y: 0, z: 0 },
      transaction: undefined,
      item_stack_request: undefined,
      block_action: undefined,
      vehicle_rotation: undefined,
      predicted_vehicle: undefined,
      analogue_move_vector: { x: 0, z: 0 },
      camera_orientation: { x: 0, y: 0, z: 1 },
      raw_move_vector: { x: 0, z: 0 }
    }
  })
  return deserializer.parsePacketBuffer(wire).data.params.input_data
}

test('sprint and sneak start held and stop ticks serialize protocol transition flags', () => {
  const inputs = new InputState()
  inputs.setFlag('sprint', true)
  inputs.setFlag('sneak', true)
  assert.deepEqual(serializeInputFlags(inputs.step()), [
    'sprinting', 'sneaking', 'sneak_current_raw', 'start_sprinting', 'sneak_down', 'sneak_pressed_raw', 'start_sneaking'
  ])
  assert.deepEqual(serializeInputFlags(inputs.step()), ['sprinting', 'sneaking', 'sneak_current_raw'])
  inputs.setFlag('sprint', false)
  inputs.setFlag('sneak', false)
  assert.deepEqual(serializeInputFlags(inputs.step()), ['stop_sprinting', 'sneak_released_raw', 'stop_sneaking'])
  assert.deepEqual(serializeInputFlags(inputs.step()), [])
})

test('sneak holds raw current across ticks and releases cleanly', () => {
  const inputs = new InputState()
  inputs.setFlag('sneak', true)
  // Press tick: cooked + edge + raw pressed/current. Internal state stays
  // true throughout; every outgoing tick is traced, not just the first.
  assert.deepEqual(
    serializeInputFlags(inputs.step()),
    ['sneaking', 'sneak_current_raw', 'sneak_down', 'sneak_pressed_raw', 'start_sneaking']
  )
  // Held ticks: steady cooked flag plus held raw current, with no press,
  // release, or stop flag contradicting the sustained sneak.
  assert.deepEqual(serializeInputFlags(inputs.step()), ['sneaking', 'sneak_current_raw'])
  assert.deepEqual(serializeInputFlags(inputs.step()), ['sneaking', 'sneak_current_raw'])
  inputs.setFlag('sneak', false)
  assert.deepEqual(serializeInputFlags(inputs.step()), ['sneak_released_raw', 'stop_sneaking'])
  assert.deepEqual(serializeInputFlags(inputs.step()), [])
})

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

test('M2 item release transaction serializes with the pinned 1.26.50 schema', () => {
  const heldItem = {
    network_id: 882,
    count: 1,
    metadata: 0,
    has_stack_id: true,
    stack_id: 34,
    block_runtime_id: 0,
    extra: { has_nbt: 0, nbt: undefined, can_place_on: [], can_destroy: [] }
  }
  const packet = createReleaseTransaction({ hotbarSlot: 2, heldItem, position: { x: 1, y: 64, z: 2 } })
  const wire = serializer.createPacketBuffer({ name: 'inventory_transaction', params: packet })
  const decoded = deserializer.parsePacketBuffer(wire).data.params.transaction
  assert.equal(decoded.transaction_type, 'item_release')
  assert.equal(decoded.transaction_data.action_type, 'release')
  assert.equal(decoded.transaction_data.hotbar_slot, 2)
  assert.equal(decoded.transaction_data.held_item.stack_id, 34)
  assert.deepEqual(decoded.transaction_data.head_pos, { x: 1, y: 65.62000274658203, z: 2 })
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

test('block interaction serializes in a server-authoritative input tick', () => {
  const transaction = createBlockInteractionInput({
      hotbarSlot: 2,
      heldItem: EMPTY_ITEM,
      position: { x: 1, y: 64, z: 2 },
      blockPosition: [3, 63, 4],
      blockRuntimeId: 987,
      face: 1
  })
  const input = deserializer.parsePacketBuffer(serializer.createPacketBuffer({
    name: 'player_auth_input',
    params: {
      pitch: 0,
      yaw: 0,
      position: { x: 1, y: 64, z: 2 },
      move_vector: { x: 0, z: 0 },
      head_yaw: 0,
      input_data: ['perform_item_interaction'],
      input_mode: 'mouse',
      play_mode: 'normal',
      interaction_model: 'crosshair',
      interact_rotation: { x: 0, z: 0 },
      tick: 1n,
      delta: { x: 0, y: 0, z: 0 },
      transaction,
      item_stack_request: undefined,
      block_action: undefined,
      vehicle_rotation: undefined,
      predicted_vehicle: undefined,
      analogue_move_vector: { x: 0, z: 0 },
      camera_orientation: { x: 0, y: 0, z: 1 },
      raw_move_vector: { x: 0, z: 0 }
    }
  })).data.params
  assert.ok(input.input_data.includes('perform_item_interaction'))
  assert.equal(input.transaction.data.action_type, 'click_block')
  assert.equal(input.transaction.data.block_runtime_id, 987)
  assert.deepEqual(input.transaction.data.click_pos, { x: 0.5, y: 1, z: 0.5 })
  assert.equal(input.block_action, undefined)

  for (const action of ['start_item_use_on', 'stop_item_use_on']) {
    const wire = serializer.createPacketBuffer({
      name: 'player_action',
      params: createBlockInteractionAction({
        runtimeEntityId: 7n,
        blockPosition: [3, 63, 4],
        face: 1,
        action
      })
    })
    const decoded = deserializer.parsePacketBuffer(wire).data.params
    assert.equal(decoded.action, action)
    assert.deepEqual(decoded.position, { x: 3, y: 63, z: 4 })
    assert.equal(decoded.face, 1)
  }
})

test('hotbar drop transactions balance inventory and world actions', () => {
  const heldItem = { network_id: 1, count: 8, metadata: 0, has_stack_id: false, block_runtime_id: 0 }
  const one = createDropTransaction({ hotbarSlot: 4, heldItem, stack: false })
  const decodedOne = deserializer.parsePacketBuffer(
    serializer.createPacketBuffer({ name: 'inventory_transaction', params: one })
  ).data.params.transaction
  assert.equal(decodedOne.transaction_type, 'normal')
  assert.equal(decodedOne.actions[0].source_type, 'container')
  assert.equal(decodedOne.actions[0].slot, 4)
  assert.equal(decodedOne.actions[0].new_item.count, 7)
  assert.equal(decodedOne.actions[1].source_type, 'world_interaction')
  assert.equal(decodedOne.actions[1].flags, 0)
  assert.equal(decodedOne.actions[1].new_item.count, 1)

  const stack = createDropTransaction({ hotbarSlot: 4, heldItem, stack: true })
  const decodedStack = deserializer.parsePacketBuffer(
    serializer.createPacketBuffer({ name: 'inventory_transaction', params: stack })
  ).data.params.transaction
  assert.equal(decodedStack.actions[0].new_item.network_id, 0)
  assert.equal(decodedStack.actions[1].new_item.count, 8)
  assert.throws(() => createDropTransaction({ hotbarSlot: 0, heldItem: EMPTY_ITEM, stack: false }), /empty/)
})
