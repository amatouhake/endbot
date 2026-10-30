// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test from 'node:test'
import { createInventoryClose, createInventoryOpen, createLegacyDrop, createLegacySwap, decodeProbeInventory } from '../scripts/inventory-stack-builders.js'
import { EMPTY_ITEM } from '../src/packets.js'

const require = createRequire(import.meta.url)
const { createSerializer, createDeserializer } = require('bedrock-protocol/src/transforms/serializer')
const serializer = createSerializer('1.26.60')
const deserializer = createDeserializer('1.26.60')
function roundTrip (name, params) {
  const bytes = serializer.createPacketBuffer({ name, params })
  const decoded = deserializer.parsePacketBuffer(bytes).data.params
  assert.deepEqual(serializer.createPacketBuffer({ name, params: decoded }), bytes)
  return decoded
}

const encodedItem = { name: 'minecraft:stone', count: 5, metadata: 7, blockRuntimeId: 23,
  nbt: { type: 'compound', value: { custom: { type: 'long', value: '123456789123456789' },
    CanDestroy: { type: 'list', value: { type: 'string', value: ['minecraft:dirt'] } } } } }
const observation = () => ({ identityId: 'bot', slots: Array(36).fill(null), equipment: Array(5).fill(null) })
const palette = new Map([['minecraft:stone', 1], ['minecraft:shield', 2]])

for (const [a, b, countA, countB] of [[9, 0, 5, 0], [9, 1, 5, 3], [0, 35, 5, 0], [9, 1, 5, 2]]) {
  test(`legacy two-container exchange ${a}/${b} counts ${countA}/${countB} round-trips exact data`, () => {
    const value = observation()
    value.slots[a] = { ...encodedItem, count: countA }
    value.slots[b] = countB ? { ...encodedItem, count: countB, name: countB === 3 ? 'minecraft:shield' : 'minecraft:stone' } : null
    const { slots } = decodeProbeInventory(value, 'bot', palette)
    const packet = createLegacySwap({ source: { container: 'inventory', slot: a },
      destination: { container: 'inventory', slot: b }, item: slots[a], destinationItem: slots[b] })
    const decoded = roundTrip('inventory_transaction', packet).transaction
    assert.equal(decoded.transaction_type, 'normal')
    assert.equal(decoded.actions.length, 2)
    const [first, second] = decoded.actions
    assert.deepEqual([first.slot, second.slot], [a, b])
    for (const action of decoded.actions) {
      assert.equal(action.source_type, 'container')
      assert.equal(action.window_id, 0)
      assert.equal(action.old_item.has_stack_id ?? false, false)
      assert.equal(action.new_item.has_stack_id ?? false, false)
    }
    assert.deepEqual(first.old_item, second.new_item)
    assert.deepEqual(first.new_item, second.old_item)
    assert.equal(first.old_item.count, countA)
    assert.equal(first.new_item.network_id, countB ? slots[b].network_id : 0)
    if (countB) assert.equal(first.new_item.count, countB)
    assert.equal(first.old_item.metadata, 7)
    assert.deepEqual(first.old_item.extra.can_destroy, ['minecraft:dirt'])
    assert.equal(slots[a].count, countA)
    const long = first.old_item.extra.nbt.nbt.value.custom.value
    assert.equal((BigInt(long[0]) << 32n) | BigInt(long[1] >>> 0), 123456789123456789n)
  })
}

test('legacy swap refuses equipment and invalid references', () => {
  for (const source of [{ container: 'armor', slot: 0 }, { container: 'inventory', slot: 36 }, {}]) {
    assert.throws(() => createLegacySwap({ source, destination: { container: 'inventory', slot: 0 } }))
  }
})

test('open inventory interact uses own runtime ID and protocol-2193 action 6', () => {
  const packet = createInventoryOpen(987n)
  const decoded = roundTrip('interact', packet)
  assert.equal(decoded.action_id, 'open_inventory')
  assert.equal(decoded.target_entity_id, 987n)
  assert.equal(decoded.has_position, false)
  for (const invalid of [undefined, '987', 0, -1n, 1.5]) assert.throws(() => createInventoryOpen(invalid))
})

test('close inventory echoes BDS window ID and type with server=false', () => {
  for (const window_id of ['inventory', 0, 7]) {
    const decoded = roundTrip('container_close', createInventoryClose({ window_id, window_type: 'inventory' }))
    assert.equal(decoded.window_id, window_id === 0 ? 'inventory' : window_id)
    assert.equal(decoded.window_type, 'inventory')
    assert.equal(decoded.server, false)
  }
  assert.throws(() => createInventoryClose({ window_id: 0 }))
  assert.throws(() => createInventoryClose())
})

test('full observation preserves all slots, equipment, NBT longs and restrictions without stack IDs', () => {
  const snapshot = observation()
  snapshot.slots = Array.from({ length: 36 }, () => encodedItem)
  snapshot.equipment = Array.from({ length: 5 }, () => encodedItem)
  snapshot.equipment[0] = { ...encodedItem, name: 'minecraft:shield', count: 1 }
  const decoded = decodeProbeInventory(snapshot, 'bot', palette)
  assert.equal(decoded.slots.length, 36)
  assert.equal(decoded.equipment.length, 5)
  for (const item of [...decoded.slots, ...decoded.equipment]) {
    assert.equal(item.has_stack_id, false)
    assert.equal(item.stack_id, undefined)
    assert.equal(item.extra.nbt.nbt.value.custom.value, 123456789123456789n)
    assert.deepEqual(item.extra.can_destroy, ['minecraft:dirt'])
    assert.equal(item.metadata, 7)
    assert.equal(item.block_runtime_id, 23)
  }
  assert.equal(decoded.equipment[0].extra.blocking_tick, 0n)
  assert.equal(decodeProbeInventory(observation(), 'bot', palette).slots[0], EMPTY_ITEM)
  assert.throws(() => decodeProbeInventory(snapshot, 'another-bot', palette))
  assert.throws(() => decodeProbeInventory({ ...snapshot, slots: [] }, 'bot', palette))
  assert.throws(() => decodeProbeInventory({ ...snapshot, equipment: [] }, 'bot', palette))
  assert.throws(() => decodeProbeInventory(snapshot, 'bot', new Map()))
})

for (const [container, slot, window] of [['inventory', 9, 0], ['offhand', 0, 119],
  ...[0, 1, 2, 3].map(slot => ['armor', slot, 120])]) {
  for (const count of [1, 3, 5]) {
    test(`legacy exact-item drop ${container}:${slot} count=${count} protocol-2193 round-trip`, () => {
      const snapshot = observation()
      snapshot.slots[9] = encodedItem
      const item = decodeProbeInventory(snapshot, 'bot', palette).slots[9]
      const packet = createLegacyDrop({ source: { container, slot }, item, count })
      assert.equal(item.count, 5)
      const decoded = roundTrip('inventory_transaction', packet)
      const [source, world] = decoded.transaction.actions
      assert.equal(decoded.transaction.transaction_type, 'normal')
      assert.equal(source.window_id, window)
      assert.equal(source.slot, slot)
      assert.equal(source.old_item.count, 5)
      assert.equal(source.old_item.has_stack_id, false)
      assert.equal(source.old_item.metadata, 7)
      assert.equal(source.old_item.block_runtime_id, 23)
      const long = source.old_item.extra.nbt.nbt.value.custom.value
      assert.equal((BigInt(long[0]) << 32n) | BigInt(long[1] >>> 0), 123456789123456789n)
      assert.equal(source.new_item.network_id, count === 5 ? 0 : 1)
      if (count < 5) assert.equal(source.new_item.count, 5 - count)
      assert.equal(world.source_type, 'world_interaction')
      assert.equal(world.flags, 0)
      assert.equal(world.old_item.network_id, 0)
      assert.equal(world.new_item.count, count)
      assert.deepEqual(world.new_item.extra, source.old_item.extra)
    })
  }
}

test('legacy builders reject empty sources, bad equipment slots and overdraw', () => {
  const source = { container: 'inventory', slot: 9 }
  for (const count of [0, -1, 6, 1.5]) {
    assert.throws(() => createLegacyDrop({ source, item: { ...EMPTY_ITEM, network_id: 1, count: 5 }, count }))
  }
  assert.throws(() => createLegacyDrop({ source, item: EMPTY_ITEM }))
  assert.throws(() => createLegacyDrop({ source: { container: 'armor', slot: 4 }, item: EMPTY_ITEM }))
  assert.throws(() => createLegacyDrop({ source: { container: 'cursor', slot: 0 }, item: EMPTY_ITEM }))
})
