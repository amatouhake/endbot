// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict'
import test from 'node:test'
import { createSwapTransaction, EMPTY_ITEM } from '../src/packets.js'
import { BedrockSession } from '../src/protocol-session.js'

test('swap validates slots, strips native IDs and leaves source items intact', () => {
  const item = { ...EMPTY_ITEM, network_id: 1, count: 5, has_stack_id: true, stack_id: 12 }
  const result = createSwapTransaction({ a: 9, b: 0, itemA: item })
  assert.equal(result.transaction.actions[0].old_item.has_stack_id, false)
  assert.equal(result.transaction.actions[0].old_item.stack_id, undefined)
  assert.equal(item.stack_id, 12)
  for (const [a, b] of [[0, 0], [-1, 1], [0, 36], [1.5, 2], ['1', 2], [true, 2]]) {
    assert.throws(() => createSwapTransaction({ a, b, itemA: item }))
  }
  assert.throws(() => createSwapTransaction({ a: 0, b: 1 }), /Both inventory slots/)
})

test('hold exact matching chooses lowest hotbar first, then lowest main; missing/invalid never mutate', () => {
  const session = new BedrockSession({}, {})
  session.client = { status: 4 }
  session.itemPalette.set('minecraft:stone', 1)
  session.inventory = Array(36).fill(EMPTY_ITEM)
  const stone = { ...EMPTY_ITEM, network_id: 1, count: 5 }
  const calls = []
  session.selectHotbar = slot => { calls.push(['hotbar', slot]); session.hotbarSlot = slot }
  session.swap = (a, b) => calls.push(['swap', a, b])
  session.inventory[13] = session.inventory[17] = stone
  session.inventory[4] = session.inventory[2] = stone
  assert.deepEqual(session.hold('stone'), { found: true, sourceSlot: 3, selectedHotbarSlot: 3 })
  assert.deepEqual(calls.pop(), ['hotbar', 2])
  session.inventory[2] = session.inventory[4] = EMPTY_ITEM
  assert.deepEqual(session.hold('minecraft:stone'), { found: true, sourceSlot: 14, selectedHotbarSlot: 3 })
  assert.deepEqual(calls.pop(), ['swap', 14, 3])
  for (const item of ['stones', 'absent']) assert.deepEqual(session.hold(item), { found: false })
  for (const item of [undefined, 1, 'Stone', 'custom:stone', 'stone dirt', 'minecraft:']) assert.throws(() => session.hold(item))
  for (const a of ['1', true, undefined, 0, 37]) {
    assert.throws(() => BedrockSession.prototype.swap.call(session, a, 2))
  }
  assert.deepEqual(calls, [])
  session.client.status = 0
  assert.throws(() => session.hold('stone'), /online/)
})
