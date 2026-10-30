// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import test from 'node:test'
import { createDropTransaction, EMPTY_ITEM } from '../src/packets.js'
import { createStackRequest, stackAction, stackSlot } from '../src/stack-requests.js'

const require = createRequire(import.meta.url)
const { createSerializer, createDeserializer } = require('bedrock-protocol/src/transforms/serializer')
const serializer = createSerializer('1.26.60') // locked BDS 1.26.52.3, protocol 2193
const deserializer = createDeserializer('1.26.60')
function roundTrip (name, params) {
  const bytes = serializer.createPacketBuffer({ name, params })
  const decoded = deserializer.parsePacketBuffer(bytes).data.params
  assert.deepEqual(serializer.createPacketBuffer({ name, params: decoded }), bytes)
  return decoded
}

for (const [from, to] of [[9, 0], [0, 35]]) {
  for (const type of ['take', 'place', 'swap']) {
    test(`${type} main/hotbar ${from} -> ${to} serializer round-trip`, () => {
      const source = stackSlot('hotbar_and_inventory', from, 123456)
      const destination = stackSlot('hotbar_and_inventory', to, 0)
      const action = stackAction(type, source, destination, 5)
      const result = roundTrip('item_stack_request', createStackRequest(-1, [action]))
      assert.equal(result.requests[0].request_id, -1)
      assert.deepEqual(result.requests[0].actions[0], action)
    })
  }
}
test('take to cursor and place from request-relative cursor ID round-trip', () => {
  const source = stackSlot('inventory', 9, 42)
  const destination = stackSlot('hotbar', 0, 0)
  const result = roundTrip('item_stack_request', createStackRequest(-3, [
    stackAction('take', source, stackSlot('cursor', 0, 0), 5),
    stackAction('place', stackSlot('cursor', 0, -3), destination, 5)
  ]))
  assert.equal(result.requests[0].actions[1].source.stack_id, -3)
})
for (const [container, slot] of [['inventory', 9], ['offhand', 0], ...[0, 1, 2, 3].map(slot => ['armor', slot])]) {
  for (const count of [1, 5]) {
    test(`drop ${container}:${slot} count ${count} serializer round-trip`, () => {
      const action = stackAction('drop', stackSlot(container, slot, 73), undefined, count)
      const result = roundTrip('item_stack_request', createStackRequest(-5, [action]))
      assert.deepEqual(result.requests[0].actions[0], action)
    })
  }
}
for (const stack of [false, true]) {
  test(`legacy non-selected main slot drop stack=${stack} serializer round-trip`, () => {
    const item = { ...EMPTY_ITEM, network_id: 1, count: 5, extra: { has_nbt: false, can_place_on: [], can_destroy: [] } }
    const decoded = roundTrip('inventory_transaction', createDropTransaction({ hotbarSlot: 9, heldItem: item, stack }))
    assert.equal(decoded.transaction.actions[0].slot, 9)
    assert.equal(decoded.transaction.actions[1].new_item.count, stack ? 5 : 1)
  })
}
test('builders reject invalid slot, count, ID and action', () => {
  for (const args of [['armor', 4, 1], ['offhand', 1, 1], ['inventory', 36, 1], ['inventory', 9, undefined]]) {
    assert.throws(() => stackSlot(...args))
  }
  const slot = stackSlot('inventory', 9, 1)
  for (const count of [0, -1, 256, 1.5]) assert.throws(() => stackAction('drop', slot, undefined, count))
  assert.throws(() => stackAction('destroy', slot, undefined, 1))
  assert.throws(() => createStackRequest(0, []))
})
