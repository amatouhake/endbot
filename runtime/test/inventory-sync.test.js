// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import test from 'node:test'
import { sameItem } from '../src/inventory-sync.js'

const extra = { has_nbt: false, can_place_on: [], can_destroy: [] }

test('an observed item equals the native one despite stack and block ids', () => {
  // Wheat shares the crop's name: the observation reports the crop block id, the wire 0.
  const native = { network_id: 337, count: 4, metadata: 0, has_stack_id: true, stack_id: 3, block_runtime_id: 0,
    extra: { ...extra, has_nbt: 0 } }
  const observed = { network_id: 337, count: 4, metadata: 0, has_stack_id: false, block_runtime_id: 1485804093, extra }
  assert.equal(sameItem(native, observed), true)
  assert.equal(sameItem(native, { ...observed, count: 3 }), false)
})

test('a native NBT long ([high, low]) equals the observed BigInt', () => {
  const nbt = value => ({ ...extra, has_nbt: true,
    nbt: { version: 1, nbt: { type: 'compound', name: '', value: {
      map_uuid: { type: 'long', value },
      ticks: { type: 'list', value: { type: 'long', value: [value] } }
    } } } })
  const item = value => ({ network_id: 420, count: 1, metadata: 0, block_runtime_id: 0, extra: nbt(value) })
  assert.equal(sameItem(item([-1, -2]), item(-2n)), true)
  assert.equal(sameItem(item([1, 5]), item((1n << 32n) + 5n)), true)
  assert.equal(sameItem(item([1, 5]), item(5n)), false)
})
