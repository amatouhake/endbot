// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test from 'node:test'
import { dropPlan, MAX_DROP_COUNT } from '../src/inventory-drops.js'
import { decodeInventory, InventoryObservation, MAX_INVENTORY_BYTES } from '../src/inventory-sync.js'
import { createDropTransaction, EMPTY_ITEM } from '../src/packets.js'

const require = createRequire(import.meta.url)
const { createSerializer, createDeserializer } = require('bedrock-protocol/src/transforms/serializer')
const serializer = createSerializer('1.26.51')
const deserializer = createDeserializer('1.26.51')
const palette = new Map([['minecraft:stone', 1], ['minecraft:shield', 2]])
const encoded = { name: 'minecraft:stone', count: 5, metadata: 2, blockRuntimeId: 1,
  nbt: { type: 'compound', value: { custom: { type: 'long', value: '9223372036854775807' },
    CanDestroy: { type: 'list', value: { type: 'string', value: ['minecraft:stone'] } } } } }
const snapshot = () => ({ identityId: 'bot', slots: Array(36).fill(null), equipment: Array(5).fill(null) })
function fragments (value, sequence = 1) {
  const data = Buffer.from(JSON.stringify(value))
  const parts = Math.ceil(data.length / 40960)
  return Array.from({ length: parts }, (_, part) => JSON.stringify({ identityId: 'bot', sequence, part, parts,
    data: data.subarray(part * 40960, (part + 1) * 40960).toString('base64') }))
}

for (const [windowId, slot] of [[0, 35], [119, 0], [120, 0], [120, 1], [120, 2], [120, 3]]) {
  test(`product legacy drop round-trips exact item data window ${windowId} slot ${slot}`, () => {
    const value = snapshot(); value.slots[0] = encoded
    const item = { ...decodeInventory(JSON.stringify(value), 'bot', palette).slots[0], has_stack_id: true, stack_id: 99 }
    for (const count of [1, 5]) {
      const packet = createDropTransaction({ windowId, slot, heldItem: item, count })
      const decoded = deserializer.parsePacketBuffer(serializer.createPacketBuffer({ name: 'inventory_transaction', params: packet })).data.params
      const [source, world] = decoded.transaction.actions
      assert.equal(source.window_id, windowId)
      assert.equal(source.slot, slot)
      assert.equal(source.old_item.has_stack_id, false)
      assert.equal(source.new_item.count, 5 - count)
      assert.equal(world.new_item.count, count)
      const long = world.new_item.extra.nbt.nbt.value.custom.value
      assert.equal((BigInt(long[0]) << 32n) | BigInt(long[1] >>> 0), 9223372036854775807n)
      assert.deepEqual(world.new_item.extra.can_destroy, ['minecraft:stone'])
      assert.equal(item.count, 5)
    }
  })
}

test('drop builder rejects invalid windows, slots, counts and empty items', () => {
  const item = { ...EMPTY_ITEM, network_id: 1, count: 5 }
  for (const options of [{ windowId: 1, slot: 0 }, { windowId: 119, slot: 1 }, { windowId: 120, slot: 4 },
    { slot: -1 }, { slot: 36 }, { slot: 0, count: 0 }, { slot: 0, count: 6 }, { slot: 0, count: 1.5 },
    { slot: 0, heldItem: EMPTY_ITEM }]) {
    assert.throws(() => createDropTransaction({ heldItem: item, count: 1, ...options }))
  }
})

test('selectors take lowest slots first, include all equipment and report shortfalls', () => {
  const inventory = Array(36).fill(EMPTY_ITEM)
  const item = { ...EMPTY_ITEM, network_id: 1, count: 5 }
  inventory[1] = inventory[11] = inventory[35] = item
  const equipment = Array(5).fill(item)
  const plan = options => dropPlan(options, inventory, equipment, 1, palette)
  assert.deepEqual(plan({}).map(p => p.count), [1])
  assert.deepEqual(plan({ stack: true }).map(p => p.count), [5])
  assert.equal(plan({ selector: '12' })[0].count, 5)
  assert.equal(plan({ selector: 'head' })[0].windowId, 120)
  assert.deepEqual(plan({ selector: 'minecraft:stone', count: 7 }).map(p => [p.label, p.count]), [['2', 5], ['12', 2]])
  assert.equal(plan({ selector: 'minecraft:stone', count: 99 }).reduce((sum, p) => sum + p.count, 0), 40)
  assert.equal(plan({ selector: 'minecraft:stone' }).length, 8)
  assert.deepEqual(plan({ selector: 'all' }).map(p => p.label), ['2', '12', '36', 'offhand', 'head', 'chest', 'legs', 'feet'])
  assert.deepEqual(plan({ selector: 'minecraft:absent' }), [])
  inventory.fill(item)
  assert.equal(plan({ selector: 'all' }).length, 41)
  for (const options of [{ selector: '37' }, { selector: 'garbage' }, { selector: 'head', count: 1 },
    { selector: 'minecraft:stone', count: 0 }, { selector: 'minecraft:stone', count: MAX_DROP_COUNT + 1 },
    { selector: 'minecraft:stone', count: 1.5 }, { selector: 'minecraft:stone', count: true }, { stack: 'true' }]) {
    assert.throws(() => plan(options))
  }
})

test('full observations reject wrong identities, malformed equipment and unknown late items atomically', () => {
  const value = snapshot(); value.slots[35] = encoded; value.equipment[0] = { ...encoded, name: 'minecraft:shield' }
  const decoded = decodeInventory(JSON.stringify(value), 'bot', palette)
  assert.equal(decoded.slots.length, 36)
  assert.equal(decoded.equipment[0].extra.blocking_tick, 0n)
  assert.throws(() => decodeInventory(JSON.stringify(value), 'other', palette))
  for (const overrides of [{ equipment: [] }, { equipment: Array(5).fill({ ...encoded, name: 'unknown' }) },
    { slots: Array(9).fill(null) }, { equipment: [...Array(4).fill(null), { ...encoded, count: 256 }] }]) {
    assert.throws(() => decodeInventory(JSON.stringify({ ...value, ...overrides }), 'bot', palette))
  }
})

test('large NBT observations assemble out of order under 60 KiB and never publish partial snapshots', () => {
  const value = snapshot()
  const large = { ...encoded, nbt: { type: 'compound', value: { text: { type: 'string', value: 'x'.repeat(32768) } } } }
  value.slots.fill(large); value.equipment.fill(large)
  const packets = fragments(value)
  const observation = new InventoryObservation()
  for (const part of packets.slice(1).reverse()) {
    assert.ok(Buffer.byteLength(part) < 60 * 1024)
    assert.equal(observation.accept(part, 'bot', palette), undefined)
  }
  const result = observation.accept(packets[0], 'bot', palette)
  assert.equal(result.slots[35].extra.nbt.nbt.value.text.value.length, 32768)
  assert.equal(result.equipment.length, 5)
  assert.equal(observation.accept(packets[0], 'bot', palette), undefined)
  const oversized = 'x'.repeat(MAX_INVENTORY_BYTES + 1)
  assert.throws(() => decodeInventory(oversized, 'bot', palette))
})

test('fragment validation rejects wrong identity, conflicting parts and bounds; newer corrections replace incomplete state', () => {
  const observation = new InventoryObservation()
  const value = snapshot()
  const part = JSON.parse(fragments(value)[0])
  for (const overrides of [{ identityId: 'other' }, { parts: 65 }, { part: -1 }, { data: '!!!' }, { sequence: 1.2 }]) {
    assert.throws(() => observation.accept(JSON.stringify({ ...part, ...overrides }), 'bot', palette))
  }
  observation.accept(JSON.stringify({ ...part, parts: 2 }), 'bot', palette)
  assert.throws(() => observation.accept(JSON.stringify({ ...part, parts: 3 }), 'bot', palette))
  assert.throws(() => observation.accept(JSON.stringify({ ...part, parts: 2, data: Buffer.from('{}').toString('base64') }), 'bot', palette))
  assert.equal(observation.accept(fragments(value, 2)[0], 'bot', palette).slots.length, 36)
  assert.throws(() => observation.accept(JSON.stringify({ ...part, sequence: 3, data: Buffer.from([0xff]).toString('base64') }), 'bot', palette), /UTF-8/)
})
