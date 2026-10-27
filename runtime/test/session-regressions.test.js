// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import test from 'node:test'

import { InputState } from '../src/actions.js'
import { EMPTY_ITEM } from '../src/packets.js'
import { BedrockSession } from '../src/protocol-session.js'

const require = createRequire(import.meta.url)
const { createSerializer, createDeserializer } = require('bedrock-protocol/src/transforms/serializer')
const serializer = createSerializer('1.26.51')
const deserializer = createDeserializer('1.26.51')
const item = { network_id: 882, count: 3, metadata: 0, has_stack_id: true, stack_id: 34, block_runtime_id: 0,
  extra: { has_nbt: 0, can_place_on: [], can_destroy: [] } }


function observationEvent (session, snapshot) {
  const identityId = session.profile.identityId
  const data = Buffer.from(JSON.stringify({ equipment: Array(5).fill(null), ...snapshot })).toString('base64')
  session.testObservationSequence = (session.testObservationSequence ?? 0) + 1
  return { event_name: 'endbot:inventory-v2', event_data: JSON.stringify({ identityId, sequence: session.testObservationSequence, part: 0, parts: 1, data }) }
}

async function fixture (t, options = {}, profile = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-regression-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const queued = []
  const client = new EventEmitter()
  client.status = 4
  client.entityId = 7n
  // Round-trip every outgoing packet through the pinned serializer, including
  // dimension acknowledgements and the packed item-use payload.
  client.queue = (name, params) => queued.push(deserializer.parsePacketBuffer(
    serializer.createPacketBuffer({ name, params })
  ).data)
  client.close = client.disconnect = () => queueMicrotask(() => client.emit('close'))
  let connectionOptions
  const session = new BedrockSession({ name: 'Alice', identityId: '00000000-0000-4000-8000-000000000001', ...profile }, {
    serverHost: 'localhost',
    ownerPrivateKeyPath: path.join(directory, 'owner-private.pem'),
    ownerPublicKeyPath: path.join(directory, 'owner-public.pem'),
    protocolLoader: async () => ({ createClient: value => { connectionOptions = value; return client } }),
    ...options
  })
  t.after(() => session.disconnect('test cleanup'))
  const connecting = session.connect()
  await new Promise(resolve => setImmediate(resolve))
  client.emit('start_game', { player_position: { x: 0, y: 64, z: 0 }, dimension: 0, rotation: { x: 0, z: 0 }, server_authoritative_block_breaking: true })
  client.emit('inventory_content', { window_id: 'inventory', input: [structuredClone(item)] })
  const inputs = new InputState()
  session.applyInputs(inputs)
  t.mock.timers.enable({ apis: ['setInterval'] })
  client.emit('spawn')
  await connecting
  function tick () {
    queued.length = 0
    t.mock.timers.tick(50)
    return queued.find(packet => packet.name === 'player_auth_input')?.params
  }
  return { client, session, inputs, queued, tick, connectionOptions }
}

const miningTarget = { blockPosition: [0, 64, 2], blockRuntimeId: 42, blockName: 'minecraft:stone', face: 2, dimension: 'Overworld' }

test('swap and main hold predict both exact stacks, announce selected equipment and accept corrections', async t => {
  const { client, session, queued } = await fixture(t)
  session.itemPalette.set('minecraft:stone', 1)
  session.itemPalette.set('minecraft:dirt', 2)
  const slots = Array(36).fill(null)
  const encoded = (name, count) => ({ name, count, metadata: 7, blockRuntimeId: 2,
    nbt: { type: 'compound', value: { custom: { type: 'string', value: 'keep exact' } } } })
  slots[0] = encoded('minecraft:dirt', 3)
  slots[13] = encoded('minecraft:stone', 5)
  const observe = () => client.emit('script_custom_event', observationEvent(session, { identityId: session.profile.identityId, slots }))
  observe()
  queued.length = 0
  session.hold('stone')
  assert.equal(session.inventory[0].network_id, 1)
  assert.equal(session.inventory[13].network_id, 2)
  assert.equal(session.inventory[13].count, 3)
  assert.equal(session.heldItem.extra.nbt.nbt.value.custom.value, 'keep exact')
  assert.equal(queued.find(p => p.name === 'inventory_transaction').params.transaction.actions.length, 2)
  assert.equal(queued.find(p => p.name === 'mob_equipment').params.item.network_id, 1)
  // BDS rejection/periodic correction restores both source and selected hand.
  observe()
  assert.equal(session.inventory[0].network_id, 2)
  assert.equal(session.inventory[13].network_id, 1)
  assert.equal(queued.at(-1).params.item.network_id, 2)
  queued.length = 0
  session.swap(1, 36)
  assert.equal(session.inventory[0].network_id, 0)
  assert.equal(session.inventory[35].network_id, 2)
  assert.equal(queued.at(-1).params.item.network_id, 0)
  observe()
  queued.length = 0
  assert.throws(() => session.swap(2, 3), /Both inventory slots/)
  assert.throws(() => session.swap(1, 1), /different/)
  assert.deepEqual(queued, [])
  // A hotbar match follows hotbar selection, without an inventory transaction.
  slots[2] = encoded('minecraft:stone', 2)
  slots[4] = encoded('minecraft:stone', 4)
  observe()
  queued.length = 0
  assert.equal(session.hold('minecraft:stone').sourceSlot, 3)
  assert.equal(session.selectedHotbarSlot(), 2)
  assert.equal(queued.some(p => p.name === 'inventory_transaction'), false)
  assert.equal(queued.at(-1).params.selected_slot, 2)
})

test('offhand equipment cannot overwrite or reannounce the main hand during use', async t => {
  const { client, session, inputs, queued, tick } = await fixture(t)
  inputs.setAction('use', 'continuous', undefined, 10)
  tick()
  const held = session.heldItem
  const active = session.activeItemUse
  queued.length = 0
  const incoming = deserializer.parsePacketBuffer(serializer.createPacketBuffer({ name: 'mob_equipment', params: {
    runtime_entity_id: 7n, window_id: 'offhand', slot: 0, selected_slot: 0, item: { ...item, network_id: 883 }
  } })).data
  client.emit(incoming.name, incoming.params)
  assert.equal(session.heldItem, held)
  assert.equal(session.selectedHotbarSlot(), 0)
  assert.equal(session.activeItemUse, active)
  assert.equal(inputs.hasTimedUse(), true)
  assert.equal(queued.length, 0)
  client.emit('mob_equipment', { runtime_entity_id: 7n, window_id: 'inventory', selected_slot: 0, item: { ...item, count: 2 } })
  assert.equal(session.heldItem.count, 2)
  assert.equal(session.inventory[0].count, 2)
  assert.equal(queued.length, 1)
  assert.equal(queued[0].params.window_id, 'inventory')
})

test('an interval use keeps its schedule when the server consumes the held stack', async t => {
  const { client, session, inputs, tick } = await fixture(t)
  inputs.setAction('use', 'interval', 40, undefined, 3)
  assert.ok(tick().input_data.includes('item_interact'))
  assert.equal(session.activeItemUse?.holdTicks, 3)
  // A bottle is drunk: the selected slot becomes air mid-press.
  client.emit('mob_equipment', { runtime_entity_id: 7n, window_id: 'inventory', selected_slot: 0, item: { ...EMPTY_ITEM } })
  assert.equal(session.activeItemUse, undefined)
  assert.equal(inputs.actionMode('use'), 'interval')
  // A refilled hand is used at the next interval instead of never again.
  client.emit('mob_equipment', { runtime_entity_id: 7n, window_id: 'inventory', selected_slot: 0, item: { ...item } })
  let presses = 0
  for (let i = 0; i < 45; i += 1) if ((tick().input_data ?? []).includes('item_interact')) presses += 1
  assert.ok(presses >= 1, `expected another press, saw ${presses}`)
})

test('an interval use skips a trigger with an empty hand instead of cancelling itself', async t => {
  const { client, session, inputs, tick } = await fixture(t)
  inputs.setAction('use', 'interval', 20, undefined, 3)
  client.emit('mob_equipment', { runtime_entity_id: 7n, window_id: 'inventory', selected_slot: 0, item: { ...EMPTY_ITEM } })
  assert.equal(tick().input_data.includes('item_interact'), false)
  assert.equal(inputs.actionMode('use'), 'interval')
  client.emit('mob_equipment', { runtime_entity_id: 7n, window_id: 'inventory', selected_slot: 0, item: { ...item } })
  let presses = 0
  for (let i = 0; i < 25; i += 1) if ((tick().input_data ?? []).includes('item_interact')) presses += 1
  assert.ok(presses >= 1, `expected another press, saw ${presses}`)
})

test('timed use retains its full duration when block interactions defer the first air use', async t => {
  const { session, inputs, queued, tick } = await fixture(t)
  for (const duration of [1, 3]) {
    inputs.setAction('use', 'continuous', undefined, duration)
    session.applyInputs(inputs)
    for (let blocked = 0; blocked < 2; blocked++) {
      session.interactBlock({ blockPosition: [0, 64, 1], blockRuntimeId: 42, face: 2 })
      const packet = tick()
      assert.equal(packet.input_data.includes('start_using_item'), false)
      assert.equal(inputs.snapshot().actions.use.remainingTicks, duration)
      assert.equal(queued.some(packet => packet.name === 'inventory_transaction'), false)
    }
    for (let held = 0; held < duration; held++) {
      const packet = tick()
      assert.equal(packet.input_data.includes('start_using_item'), held === 0)
      assert.equal(packet.transaction.data.action_type, 'click_air')
      assert.equal(queued.some(packet => packet.name === 'inventory_transaction'), false)
    }
    tick()
    assert.equal(queued.filter(packet => packet.name === 'inventory_transaction').length, 1)
    assert.equal(queued.find(packet => packet.name === 'inventory_transaction').params.transaction.transaction_type, 'item_release')
    tick()
    assert.equal(queued.some(packet => packet.name === 'inventory_transaction'), false)
  }
})

test('stopping deferred use does not release an item that never started charging', async t => {
  const { session, inputs, queued, tick } = await fixture(t)
  inputs.setAction('use', 'continuous', undefined, 1)
  session.interactBlock({ blockPosition: [0, 64, 1], blockRuntimeId: 42, face: 2 })
  tick()
  inputs.setAction('use', 'stop')
  session.applyInputs(inputs)
  assert.equal(queued.some(packet => packet.name === 'inventory_transaction'), false)
  assert.equal(tick().input_data.includes('start_using_item'), false)
})

test('timed empty-hand use remains a no-op instead of waiting for a later pickup', async t => {
  const { client, session, inputs, queued, tick } = await fixture(t)
  client.emit('inventory_content', { window_id: 'inventory', input: [] })
  inputs.setAction('use', 'continuous', undefined, 3)
  session.applyInputs(inputs)
  assert.equal(tick().input_data.includes('start_using_item'), false)
  assert.equal(inputs.hasTimedUse(), false)
  client.emit('inventory_slot', { window_id: 'inventory', slot: 0, item: structuredClone(item) })
  assert.equal(tick().input_data.includes('start_using_item'), false)
  assert.equal(queued.some(packet => packet.name === 'inventory_transaction'), false)
})

test('timed use holds for N ticks and releases once while movement continues', async t => {
  const { session, inputs, queued, tick } = await fixture(t)
  inputs.setMovement('forward')
  inputs.setAction('use', 'continuous', undefined, 3)
  session.applyInputs(inputs)
  for (let i = 0; i < 3; i++) {
    const packet = tick()
    assert.ok(packet.input_data.includes('item_interact'))
    assert.equal(queued.some(packet => packet.name === 'inventory_transaction'), false)
  }
  const expired = tick()
  assert.ok(expired.input_data.includes('up'))
  assert.equal(expired.input_data.includes('item_interact'), false)
  assert.equal(queued.filter(packet => packet.name === 'inventory_transaction').length, 1)
  assert.equal(queued.find(packet => packet.name === 'inventory_transaction').params.transaction.transaction_type, 'item_release')
  tick()
  assert.equal(queued.some(packet => packet.name === 'inventory_transaction'), false)
})

test('timed use stops on reselection and replacement commands own their new deadline', async t => {
  const { session, inputs, queued, tick } = await fixture(t)
  inputs.setAction('use', 'continuous', undefined, 10)
  tick()
  inputs.setAction('use', 'continuous', undefined, 2)
  session.applyInputs(inputs)
  assert.equal(queued.at(-1).params.transaction.transaction_type, 'item_release')
  tick()
  tick()
  tick()
  assert.equal(inputs.actionMode('use'), undefined)
  assert.equal(queued.filter(packet => packet.name === 'inventory_transaction').length, 1)
  inputs.setAction('use', 'continuous', undefined, 10)
  tick()
  session.selectHotbar(0)
  assert.equal(inputs.actionMode('use'), undefined)
  assert.equal(tick().input_data.includes('item_interact'), false)
})

test('repeating an unlimited continuous use command does not release a charged item', async t => {
  const { session, inputs, queued, tick } = await fixture(t)
  inputs.setAction('use', 'continuous')
  tick()
  queued.length = 0
  inputs.setAction('use', 'continuous')
  session.applyInputs(inputs)
  assert.equal(queued.length, 0)
  tick()
  assert.equal(queued.some(packet => packet.name === 'inventory_transaction'), false)
})

test('timed use stop releases and dimension/death discard the old deadline', async t => {
  const { client, session, inputs, queued, tick } = await fixture(t)
  for (const stop of [() => inputs.setAction('use', 'stop'), () => inputs.stopAll()]) {
    inputs.setAction('use', 'continuous', undefined, 10)
    tick()
    stop()
    session.applyInputs(inputs)
    assert.equal(queued.at(-1).params.transaction.transaction_type, 'item_release')
    assert.equal(tick().input_data.includes('item_interact'), false)
  }
  for (const end of [
    () => client.emit('change_dimension', { dimension: 0, position: { x: 0, y: 64, z: 0 } }),
    () => client.emit('set_health', { health: 0 })
  ]) {
    inputs.setAction('use', 'continuous', undefined, 10)
    tick()
    end()
    assert.equal(inputs.actionMode('use'), undefined)
    assert.equal(tick().input_data.includes('item_interact'), false)
  }
})

test('native selected-slot changes announce equipment before identical plugin observations', async t => {
  const { client, session, queued } = await fixture(t)
  const publishEmpty = () => client.emit('script_custom_event', {
    ...observationEvent(session, { identityId: session.profile.identityId, slots: Array(36).fill(null) })
  })
  queued.length = 0
  client.emit('inventory_slot', { window_id: 'inventory', slot: 1, item: EMPTY_ITEM })
  assert.equal(queued.length, 0)
  // Throw/consumption/tool break: the native update already emptied the cache.
  client.emit('inventory_slot', { window_id: 'inventory', slot: 0, item: EMPTY_ITEM })
  assert.equal(session.heldItem.network_id, 0)
  assert.equal(queued.length, 1)
  assert.equal(queued[0].name, 'mob_equipment')
  assert.equal(queued[0].params.item.network_id, 0)
  publishEmpty()
  publishEmpty()
  assert.equal(queued.length, 1)
  // A delayed self-equipment packet must not keep the hand stale when the
  // next authoritative snapshot matches an already-empty inventory cache.
  client.emit('mob_equipment', { runtime_entity_id: 7n, window_id: 'inventory', selected_slot: 0, item: structuredClone(item) })
  publishEmpty()
  assert.equal(session.heldItem.network_id, 0)
  assert.equal(queued.at(-1).params.item.network_id, 0)
  queued.length = 0
  client.emit('inventory_content', { window_id: 'inventory', input: [structuredClone(item)] })
  assert.equal(queued.length, 1)
  client.emit('inventory_slot', { window_id: 'inventory', slot: 0, item: { ...item, count: 2 } })
  assert.equal(queued.length, 2)
  assert.equal(queued.at(-1).params.item.count, 2)
  session.selectHotbar(0)
  assert.equal(queued.filter(packet => packet.params.window_id === 'inventory').length, 3) // Explicit reselection still forces an announcement.
})

test('BDS hotbar observations make same-session pickups usable and correct rejected drops', async t => {
  const { client, session, queued } = await fixture(t)
  client.emit('item_registry', { itemstates: [{ name: 'minecraft:diamond_sword', runtime_id: 882 }] })
  client.emit('inventory_content', { window_id: 'inventory', input: [] })
  const slots = Array(36).fill(null)
  slots[0] = { name: 'minecraft:diamond_sword', count: 3, metadata: 0, blockRuntimeId: 0,
    nbt: { type: 'compound', value: { Damage: { type: 'int', value: 7 },
      bytes: { type: 'byteArray', value: [-1, 0, 127] },
      display: { type: 'compound', value: { Name: { type: 'string', value: '拾った道具' } } },
      map_uuid: { type: 'long', value: '123456789123456789' },
      ench: { type: 'list', value: { type: 'compound', value: [{ id: { type: 'short', value: 9 }, lvl: { type: 'short', value: 2 } }] } } } } }
  const publish = (overrides = {}) => {
    const params = observationEvent(session, { identityId: session.profile.identityId, slots, ...overrides })
    const wire = deserializer.parsePacketBuffer(serializer.createPacketBuffer({ name: 'script_custom_event', params })).data
    client.emit(wire.name, wire.params)
  }
  queued.length = 0
  publish()
  assert.equal(session.inventory[0].count, 3)
  assert.equal(queued.at(-1).name, 'mob_equipment')
  assert.equal(queued.at(-1).params.item.extra.nbt.nbt.value.Damage.value, 7)
  session.dropSelected(false)
  assert.equal(session.inventory[0].count, 2)
  assert.equal(queued.findLast(packet => packet.name === 'inventory_transaction').params.transaction.actions[1].new_item.count, 1)
  // A canceled/rejected transaction leaves BDS's count unchanged: a fresh
  // observation restores it, rather than trusting optimistic drop forever.
  publish()
  assert.equal(session.inventory[0].count, 3)
  session.dropSelected(true)
  slots[0] = null
  publish()
  assert.equal(session.heldItem.network_id, 0)
  assert.equal(queued.findLast(packet => packet.name === 'mob_equipment').params.item.network_id, 0)
  const unchanged = queued.length
  publish()
  assert.equal(queued.length, unchanged)
})

test('full inventory drops predict every window, announce offhand, and accept authoritative corrections', async t => {
  const { client, session, queued } = await fixture(t)
  client.emit('item_registry', { itemstates: [{ name: 'minecraft:stone', runtime_id: 882 }] })
  const slots = Array(36).fill(null)
  const encoded = { name: 'minecraft:stone', count: 5, metadata: 0, blockRuntimeId: 0, nbt: { type: 'compound', value: {} } }
  slots[0] = slots[11] = encoded
  const equipment = Array(5).fill(encoded)
  const publish = () => client.emit('script_custom_event', observationEvent(session, { identityId: session.profile.identityId, slots, equipment }))
  publish()
  queued.length = 0
  const result = session.drop({ selector: 'minecraft:stone', count: 7 })
  assert.deepEqual(result.drops, [{ slot: '1', item: 'minecraft:stone', count: 5 }, { slot: '12', item: 'minecraft:stone', count: 2 }])
  assert.equal(session.inventory[0].count, 0)
  assert.equal(session.inventory[11].count, 3)
  assert.equal(session.heldItem.count, 0)
  assert.equal(queued.filter(packet => packet.name === 'inventory_transaction').length, 2)
  publish() // BDS canceled/rejected either request: restore both exact stacks.
  assert.equal(session.inventory[11].count, 5)
  assert.equal(session.heldItem.count, 5)
  queued.length = 0
  const all = session.drop({ selector: 'all' })
  assert.equal(all.droppedSlots, 7)
  assert.equal(all.droppedCount, 35)
  assert.ok(session.equipment.every(item => item.count === 0))
  const transactions = queued.filter(packet => packet.name === 'inventory_transaction')
  assert.deepEqual(transactions.map(packet => packet.params.transaction.actions[0].window_id), [0, 0, 119, 120, 120, 120, 120])
  assert.ok(queued.some(packet => packet.name === 'mob_equipment' && packet.params.window_id === 'offhand' && packet.params.item.count === 0))
  assert.equal(session.drop({ selector: 'all' }).droppedSlots, 0)
  publish()
  assert.equal(session.equipment[1].count, 5) // Helmet receives the same correction.
})

test('an unchanged hotbar observation keeps native stack IDs and an active use', async t => {
  const { client, session, inputs, queued, tick } = await fixture(t)
  client.emit('item_registry', { itemstates: [{ name: 'minecraft:diamond_sword', runtime_id: 882 }] })
  const original = session.inventory[0]
  inputs.setAction('use', 'continuous')
  tick()
  const slots = Array(36).fill(null)
  // Native packets carry has_nbt as 0; the observation decodes to false.
  slots[0] = { name: 'minecraft:diamond_sword', count: 3, metadata: 0, blockRuntimeId: 0, nbt: { type: 'compound', value: {} } }
  queued.length = 0
  const publish = () => client.emit('script_custom_event', {
    ...observationEvent(session, { identityId: session.profile.identityId, slots })
  })
  publish()
  assert.equal(session.inventory[0], original)
  assert.equal(session.inventory[0].stack_id, 34)
  assert.equal(queued.some(packet => packet.name === 'mob_equipment'), false)
  inputs.setAction('use', 'stop')
  session.applyInputs(inputs)
  assert.equal(queued.at(-1).params.transaction.transaction_type, 'item_release')
  // A differing stack (count) is still a real change.
  slots[0].count = 2
  publish()
  assert.equal(session.inventory[0].count, 2)
})

test('unknown, partial and wrong-Bot inventory observations leave the good cache intact', async t => {
  const { client, session } = await fixture(t)
  client.emit('item_registry', { itemstates: [{ name: 'minecraft:diamond_sword', runtime_id: 882 }] })
  const original = session.inventory[0]
  const invalidSlots = Array(36).fill(null)
  // Even a valid earlier slot must not be cleared if a later slot is bad.
  invalidSlots[1] = { name: 'minecraft:diamond_sword', count: 1, metadata: 0, blockRuntimeId: 0,
    nbt: { type: 'compound', value: { Damage: { type: 'int', value: 'invalid' } } } }
  for (const snapshot of [{ identityId: 'other', slots: Array(36).fill(null) },
    { identityId: session.profile.identityId, slots: [null] },
    { identityId: session.profile.identityId, slots: invalidSlots },
    { identityId: session.profile.identityId, slots: Array(36).fill({ name: 'unknown' }) }]) {
    client.emit('script_custom_event', observationEvent(session, snapshot))
    assert.equal(session.inventory[0], original)
  }
  client.emit('script_custom_event', { event_name: 'endbot:inventory-v2', event_data: '{' })
  assert.equal(session.inventory[0], original)
})

test('continuous mining waits, retargets after BDS updates, and rejects stopped/replaced loop messages', async t => {
  const { client, session, tick } = await fixture(t)
  session.startMiningLoop()
  const loopId = session.miningStatus().loopId
  assert.equal(session.miningStatus().state, 'waiting')
  assert.equal(tick().block_action, undefined)
  session.continueMining({ loopId, ...miningTarget })
  assert.deepEqual(tick().block_action.map(action => action.action), ['start_break'])
  client.emit('update_block', { position: { x: 0, y: 64, z: 2 }, block_runtime_id: 0, layer: 0 })
  assert.equal(session.miningStatus().state, 'waiting')
  session.continueMining({ loopId, ...miningTarget, blockPosition: [0, 64, 3] })
  assert.deepEqual(tick().block_action.map(action => action.action), ['abort_break', 'start_break'])
  session.stopMining()
  assert.equal(session.continueMining({ loopId, ...miningTarget }), false)
  assert.deepEqual(tick().block_action.map(action => action.action), ['abort_break'])
  session.startMiningLoop()
  assert.notEqual(session.miningStatus().loopId, loopId)
  assert.equal(session.continueMining({ loopId, ...miningTarget }), false)
  assert.equal(tick().block_action, undefined)
})

test('continuous mining expires without controller heartbeats, including while waiting', async t => {
  const { session, tick } = await fixture(t)
  for (const active of [false, true]) {
    session.startMiningLoop()
    const loopId = session.miningStatus().loopId
    if (active) session.continueMining({ loopId, ...miningTarget })
    session.miningLoop.leaseTicks = 198
    tick()
    session.continueMining({ loopId })
    assert.equal(session.miningLoop.leaseTicks, 0)
    session.miningLoop.leaseTicks = 199
    tick()
    assert.equal(session.miningStatus().state, 'failed')
    assert.equal(session.miningStatus().continuous, undefined)
    assert.equal(session.continueMining({ loopId, ...miningTarget }), false)
  }
})

test('continuous mining survives hotbar, teleport and dimension changes', async t => {
  const { client, session } = await fixture(t)
  session.startMiningLoop()
  const loopId = session.miningStatus().loopId
  for (const change of [() => session.selectHotbar(0),
    () => client.emit('move_player', { runtime_id: 7n, position: { x: 0, y: 64, z: 0 }, mode: 'teleport', on_ground: true, tick: 0n }),
    () => client.emit('change_dimension', { dimension: 0, position: { x: 0, y: 64, z: 0 } })]) {
    session.continueMining({ loopId, ...miningTarget })
    change()
    assert.equal(session.miningStatus().loopId, loopId)
    assert.equal(session.continueMining({ loopId }), true)
  }
})

test('continuous mining ends on death and disconnect', async t => {
  const { client, session } = await fixture(t)
  for (const end of [() => client.emit('set_health', { health: 0 }), () => session.disconnect('test')]) {
    session.startMiningLoop()
    const loopId = session.miningStatus().loopId
    end()
    assert.equal(session.miningStatus().state, 'canceled')
    assert.equal(session.continueMining({ loopId, ...miningTarget }), false)
  }
})

test('mining sends one packed start action and waits for server block update', async t => {
  const { client, session, tick } = await fixture(t)
  session.startMining(miningTarget)
  const start = tick()
  assert.ok(start.input_data.includes('block_action'))
  assert.deepEqual(start.block_action.map(action => action.action), ['start_break'])
  // BDS tracks progress after start_break; repeating an action restarts its crack.
  const held = tick()
  assert.equal(held.block_action, undefined)
  assert.equal(session.miningStatus().state, 'active')
  client.emit('update_block', { position: { x: 0, y: 64, z: 2 }, block_runtime_id: 42, layer: 0 })
  client.emit('update_block', { position: { x: 1, y: 64, z: 2 }, block_runtime_id: 0, layer: 0 })
  client.emit('update_block', { position: { x: 0, y: 64, z: 2 }, block_runtime_id: 0, layer: 1 })
  assert.equal(session.miningStatus().state, 'active')
  client.emit('update_block', { position: { x: 0, y: 64, z: 2 }, block_runtime_id: 0, layer: 0 })
  assert.equal(session.miningStatus().state, 'completed')
  assert.deepEqual(tick().block_action.map(action => action.action), ['abort_break'])
  assert.equal(tick().block_action, undefined)
})

test('a held-item change restarts the current break and keeps the continuous loop', async t => {
  const { client, session, tick } = await fixture(t)
  session.startMiningLoop()
  const loopId = session.miningStatus().loopId
  session.continueMining({ loopId, ...miningTarget })
  tick()
  // A pickup or broken tool replaces the selected slot's item.
  client.emit('inventory_slot', { window_id: 'inventory', slot: 0, item: { ...structuredClone(item), network_id: 1 } })
  assert.equal(session.miningStatus().state, 'active')
  assert.equal(session.miningStatus().loopId, loopId)
  assert.deepEqual(tick().block_action.map(action => action.action), ['abort_break', 'start_break'])
  assert.equal(tick().block_action, undefined)
})

test('mining accepts the pinned wire dimension names and Endstone namespace names', async t => {
  const { session, tick } = await fixture(t)
  for (const [wire, plugin] of [['overworld', 'minecraft:overworld'], ['nether', 'Nether'], ['end', 'TheEnd']]) {
    session.dimension = wire
    session.startMining({ ...miningTarget, dimension: plugin })
    tick()
    session.stopMining()
    tick()
  }
})

test('stop before the first tick cancels without starting a break', async t => {
  const { session, tick } = await fixture(t)
  session.startMining(miningTarget)
  session.stopMining()
  assert.equal(tick().block_action, undefined)
  assert.equal(session.miningStatus().state, 'canceled')
})

test('hotbar changes restart the break, and a dimension change discards old actions', async t => {
  const { client, session, tick } = await fixture(t)
  session.startMining(miningTarget)
  tick()
  session.selectHotbar(0)
  assert.equal(session.miningStatus().state, 'active')
  assert.deepEqual(tick().block_action.map(action => action.action), ['abort_break', 'start_break'])
  session.startMining(miningTarget)
  tick()
  client.emit('change_dimension', { dimension: 1, position: { x: 40, y: 90, z: 40 } })
  assert.equal(tick().block_action, undefined)
  assert.equal(session.miningStatus().reason, 'dimension changed')
})

test('mining validates input and cancels on reach, death and timeout without disconnecting', async t => {
  const { client, session, tick } = await fixture(t)
  for (const overrides of [{ blockPosition: [0.5, 64, 2] }, { blockPosition: [0, 64, 20] }, { face: 6 }, { dimension: 'Nether' }]) {
    assert.throws(() => session.startMining({ ...miningTarget, ...overrides }))
  }
  session.startMining(miningTarget)
  tick()
  session.position.x = 20
  assert.deepEqual(tick().block_action.map(action => action.action), ['abort_break'])
  assert.equal(session.miningStatus().state, 'failed')
  session.position = { x: 0, y: 64, z: 0 }
  session.startMining(miningTarget)
  session.mining.ticks = 12_000
  tick()
  assert.match(session.miningStatus().reason, /timed out/)
  session.startMining(miningTarget)
  client.emit('set_health', { health: 0 })
  assert.equal(tick().block_action, undefined)
  assert.equal(session.miningStatus().reason, 'respawning')
  assert.equal(session.transportClosed, false)
})

test('held use sends packed press/hold ticks then a single release', async t => {
  const { inputs, session, queued, tick } = await fixture(t)
  inputs.setAction('use', 'continuous')
  const press = tick()
  assert.ok(press.input_data.includes('item_interact'))
  assert.ok(press.input_data.includes('start_using_item'))
  assert.equal(press.transaction.data.action_type, 'click_air')
  assert.equal(press.transaction.data.trigger_type, 'player_input')
  for (let index = 0; index < 40; index++) {
    const held = tick()
    assert.ok(held.input_data.includes('item_interact'))
    assert.equal(held.input_data.includes('start_using_item'), false)
    assert.equal(held.transaction.data.trigger_type, 'simulation_tick')
    assert.equal(queued.some(packet => packet.name === 'inventory_transaction'), false)
  }
  inputs.setAction('use', 'stop')
  session.applyInputs(inputs)
  assert.equal(queued.filter(packet => packet.name === 'inventory_transaction').length, 1)
  assert.equal(queued.at(-1).params.transaction.transaction_type, 'item_release')
  const stopped = tick()
  assert.equal(stopped.transaction, undefined)
})

test('switching hotbar releases the old item before announcing the new slot', async t => {
  const { session, inputs, queued, tick } = await fixture(t)
  inputs.setAction('use', 'continuous')
  tick()
  queued.length = 0
  session.selectHotbar(1)
  assert.deepEqual(queued.map(packet => packet.name), ['inventory_transaction', 'mob_equipment'])
  assert.equal(queued[0].params.transaction.transaction_data.hotbar_slot, 0)
  assert.equal(tick().transaction, undefined)
})

test('dimension changes acknowledge loading and resume movement from the new position', async t => {
  const { client, inputs, session, queued, tick } = await fixture(t, { chunkRadius: 3 })
  inputs.setMovement('forward')
  tick()
  for (const dimension of [1, 2, 0]) {
    queued.length = 0
    client.emit('change_dimension', { dimension, position: { x: 40, y: 90, z: 60 }, respawn: false, loading_screen_id: 23 })
    assert.ok(queued.some(packet => packet.name === 'player_action' && packet.params.action === 'dimension_change_ack'))
    assert.deepEqual(queued.filter(packet => packet.name === 'serverbound_loading_screen').map(packet =>
      [packet.params.type, packet.params.loading_screen_id]), [[1, 23], [2, 23]])
    assert.equal(queued.find(packet => packet.name === 'request_chunk_radius').params.chunk_radius, 3)
    assert.equal(session.entities.size, 0)
    const after = tick()
    assert.equal(after.position.x, 40)
    assert.ok(after.position.z > 60)
    assert.ok(after.position.y < 90)
    assert.ok(after.input_data.includes('handled_teleport'))
    assert.equal(tick().input_data.includes('handled_teleport'), false)
    client.emit('correct_player_move_prediction', { prediction_type: 'player', position: { x: 40, y: 64, z: 60 },
      delta: { x: 0, y: 0, z: 0 }, on_ground: true, tick: 100n })
    inputs.setAction('jump')
    assert.ok(tick().position.y > 64)
  }
})

test('server motion affects the Bot and teleport clears the old impulse', async t => {
  const { client, tick } = await fixture(t)
  client.emit('set_entity_motion', { runtime_entity_id: 99n, velocity: { x: 8, y: 8, z: 8 } })
  assert.deepEqual(tick().position, { x: 0, y: 64, z: 0 })
  client.emit('set_entity_motion', { runtime_entity_id: 7n, velocity: { x: 0.5, y: 0.4, z: -0.25 } })
  const knocked = tick()
  assert.ok(knocked.position.x > 0)
  assert.ok(knocked.position.y > 64)
  assert.ok(knocked.position.z < 0)
  client.emit('change_dimension', { dimension: 1, position: { x: 30, y: 90, z: 30 }, respawn: false })
  const after = tick()
  assert.equal(after.position.x, 30)
  assert.equal(after.position.z, 30)
})

test('small chunk radius is used by upstream initial request and on spawn', async t => {
  const { client, queued } = await fixture(t)
  assert.equal(client.viewDistance, 5)
  assert.equal(queued.find(packet => packet.name === 'request_chunk_radius').params.chunk_radius, 5)
})

test('per-Bot radius overrides default and live changes survive dimension requests', async t => {
  const { session, client, queued } = await fixture(t, { chunkRadius: 6 }, { chunkRadius: 12 })
  assert.equal(client.viewDistance, 12)
  assert.equal(queued.find(packet => packet.name === 'request_chunk_radius').params.chunk_radius, 12)
  client.emit('chunk_radius_update', { chunk_radius: 10 })
  assert.equal(session.serverChunkRadius, 10)
  queued.length = 0
  session.setChunkRadius(8)
  assert.equal(client.viewDistance, 8)
  assert.equal(session.serverChunkRadius, undefined)
  assert.equal(queued[0].params.chunk_radius, 8)
  queued.length = 0
  client.emit('change_dimension', { dimension: 1, position: { x: 0, y: 160, z: 0 }, respawn: false })
  assert.equal(queued.find(packet => packet.name === 'request_chunk_radius').params.chunk_radius, 8)
})

test('server consumption stops stale use and updates release item counts', async t => {
  const { client, inputs, session, tick, queued } = await fixture(t)
  inputs.setAction('use', 'continuous')
  tick()
  client.emit('inventory_slot', { window_id: 'inventory', slot: 0, item: { ...item, count: 2 } })
  assert.equal(tick().transaction.data.held_item.count, 2)
  inputs.stopAll()
  session.applyInputs(inputs)
  assert.equal(queued.at(-1).params.transaction.transaction_data.held_item.count, 2)
  inputs.setAction('use', 'continuous')
  tick()
  client.emit('inventory_slot', { window_id: 'inventory', slot: 0, item: EMPTY_ITEM })
  assert.equal(tick().transaction, undefined)
})

test('interval use releases each tap and preserves its scheduling', async t => {
  const { inputs, tick, queued } = await fixture(t)
  inputs.setAction('use', 'interval', 3)
  assert.equal(tick().transaction.data.trigger_type, 'player_input')
  assert.equal(tick().transaction, undefined)
  assert.equal(queued[0].params.transaction.transaction_type, 'item_release')
  assert.equal(tick().transaction, undefined)
  assert.equal(tick().transaction.data.trigger_type, 'player_input')
})

test('a one-shot air use is deferred when block interaction occupies its tick', async t => {
  const { inputs, session, tick } = await fixture(t)
  session.interactBlock({ blockPosition: [0, 63, 1], blockRuntimeId: 1, face: 1 })
  inputs.setAction('use')
  assert.equal(tick().transaction.data.action_type, 'click_block')
  const air = tick()
  assert.equal(air.transaction.data.action_type, 'click_air')
  assert.ok(air.input_data.includes('start_using_item'))
  assert.equal(tick().transaction, undefined)
})

function releases (queued) {
  return queued.filter(packet => packet.name === 'inventory_transaction' &&
    packet.params.transaction.transaction_type === 'item_release').length
}

test('use hold presses for N transactions, releases once, and a one-shot hold then ends', async t => {
  const { session, inputs, queued, tick } = await fixture(t)
  inputs.setMovement('forward')
  inputs.setAction('use', 'once', undefined, undefined, 3)
  session.applyInputs(inputs)
  for (let held = 0; held < 3; held++) {
    const packet = tick()
    assert.equal(packet.input_data.includes('start_using_item'), held === 0)
    assert.ok(packet.input_data.includes('item_interact'))
    assert.equal(releases(queued), 0)
  }
  const released = tick()
  assert.equal(releases(queued), 1)
  assert.equal(released.input_data.includes('item_interact'), false)
  assert.ok(released.input_data.includes('up'))
  assert.equal(inputs.actionMode('use'), undefined)
  tick()
  assert.equal(queued.some(packet => packet.name === 'inventory_transaction'), false)
})

test('repeated holds leave a release tick and for releases a hold in progress', async t => {
  const { session, inputs, queued, tick } = await fixture(t)
  inputs.setAction('use', 'continuous', undefined, 6, 3)
  session.applyInputs(inputs)
  const log = []
  for (let i = 0; i < 7; i++) {
    const packet = tick()
    log.push(packet.input_data.includes('start_using_item') ? 'start'
      : packet.input_data.includes('item_interact') ? 'hold' : releases(queued) ? 'release' : 'idle')
  }
  // Presses at 1 and 5 (hold 3 + one release tick); for 6 cuts the second hold.
  assert.deepEqual(log, ['start', 'hold', 'hold', 'release', 'start', 'hold', 'release'])
  assert.equal(inputs.actionMode('use'), undefined)
  tick()
  assert.equal(queued.some(packet => packet.name === 'inventory_transaction'), false)
})

test('interval holds do not release on the next tick and a new command releases a hold', async t => {
  const { session, inputs, queued, tick } = await fixture(t)
  inputs.setAction('use', 'interval', 5, undefined, 3)
  session.applyInputs(inputs)
  tick()
  tick()
  assert.equal(releases(queued), 0)
  inputs.setAction('use', 'interval', 5, undefined, 3)
  session.applyInputs(inputs)
  assert.equal(releases(queued), 1)
  inputs.setAction('use', 'once', undefined, undefined, 4)
  tick()
  session.selectHotbar(0)
  assert.equal(inputs.actionMode('use'), undefined)
})

test('destroy continuous for N ends the loop and aborts the block in progress', async t => {
  const { session, tick } = await fixture(t)
  assert.throws(() => session.startMiningLoop(0), /duration/)
  session.startMiningLoop(3)
  const loopId = session.miningStatus().loopId
  assert.equal(session.miningStatus().remainingTicks, 3)
  session.continueMining({ loopId, ...miningTarget })
  assert.deepEqual(tick().block_action.map(action => action.action), ['start_break'])
  tick()
  tick()
  assert.equal(session.miningStatus().state, 'active')
  assert.deepEqual(tick().block_action.map(action => action.action), ['abort_break'])
  assert.equal(session.miningStatus().state, 'completed')
  assert.equal(session.miningStatus().reason, 'duration elapsed')
  assert.equal(session.miningStatus().loopId, undefined)
  assert.equal(session.continueMining({ loopId, ...miningTarget }), false)
})

test('destroy continuous count ends the loop after that many completed blocks', async t => {
  const { client, session, tick } = await fixture(t)
  assert.throws(() => session.startMiningLoop(undefined, 0), /block count/)
  assert.throws(() => session.startMiningLoop(undefined, 1.5), /block count/)
  session.startMiningLoop(undefined, 2)
  const loopId = session.miningStatus().loopId
  assert.deepEqual([session.miningStatus().blocksBroken, session.miningStatus().blockCount], [0, 2])
  session.continueMining({ loopId, ...miningTarget })
  assert.deepEqual(tick().block_action.map(action => action.action), ['start_break'])
  client.emit('update_block', { position: { x: 0, y: 64, z: 2 }, block_runtime_id: 0, layer: 0 })
  // One block is gone but the budget is not spent: the loop waits for the
  // next target, and waiting does not count against the block budget.
  assert.equal(session.miningStatus().state, 'waiting')
  assert.equal(session.miningStatus().loopId, loopId)
  assert.deepEqual([session.miningStatus().blocksBroken, session.miningStatus().blockCount], [1, 2])
  session.continueMining({ loopId, ...miningTarget, blockPosition: [0, 64, 3] })
  // The finished block's cleanup abort rides the next input tick with the new
  // target's start, as for a plain continuous retarget.
  assert.deepEqual(tick().block_action.map(action => action.action), ['abort_break', 'start_break'])
  client.emit('update_block', { position: { x: 0, y: 64, z: 3 }, block_runtime_id: 0, layer: 0 })
  const status = session.miningStatus()
  assert.equal(status.state, 'completed')
  assert.equal(status.reason, 'block count reached')
  assert.equal(status.blocksBroken, 2)
  assert.equal(status.blockCount, 2)
  assert.equal(status.loopId, undefined)
  assert.equal(status.continuous, undefined)
  assert.equal(session.continueMining({ loopId, ...miningTarget }), false)
  // The last block's cleanup abort still drains; no new break starts.
  assert.deepEqual(tick().block_action.map(action => action.action), ['abort_break'])
  assert.equal(tick().block_action, undefined)
})
