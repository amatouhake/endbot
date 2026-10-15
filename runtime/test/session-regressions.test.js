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

test('continuous mining cancels while waiting on hotbar, dimension, death and disconnect', async t => {
  const { client, session } = await fixture(t)
  for (const cancel of [() => session.selectHotbar(0),
    () => client.emit('change_dimension', { dimension: 0, position: { x: 0, y: 64, z: 0 } }),
    () => client.emit('set_health', { health: 0 }), () => session.disconnect('test')]) {
    session.startMiningLoop()
    const loopId = session.miningStatus().loopId
    cancel()
    assert.equal(session.miningStatus().state, 'canceled')
    assert.equal(session.continueMining({ loopId, ...miningTarget }), false)
  }
})

test('mining sends packed start/continue actions and waits for server block update', async t => {
  const { client, session, tick } = await fixture(t)
  session.startMining(miningTarget)
  const start = tick()
  assert.ok(start.input_data.includes('block_action'))
  assert.deepEqual(start.block_action.map(action => action.action), ['start_break'])
  const held = tick()
  assert.deepEqual(held.block_action.map(action => action.action), ['continue_break'])
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

test('a held-item change cancels the current break but keeps the continuous loop', async t => {
  const { client, session, tick } = await fixture(t)
  session.startMiningLoop()
  const loopId = session.miningStatus().loopId
  session.continueMining({ loopId, ...miningTarget })
  tick()
  // A pickup or broken tool replaces the selected slot's item.
  client.emit('inventory_slot', { window_id: 'inventory', slot: 0, item: { ...structuredClone(item), network_id: 1 } })
  assert.equal(session.miningStatus().state, 'waiting')
  assert.equal(session.miningStatus().loopId, loopId)
  assert.deepEqual(tick().block_action.map(action => action.action), ['abort_break'])
  assert.equal(session.continueMining({ loopId, ...miningTarget }), true)
  assert.deepEqual(tick().block_action.map(action => action.action), ['start_break'])
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

test('hotbar changes abort mining, and a dimension change discards old actions', async t => {
  const { client, session, tick } = await fixture(t)
  session.startMining(miningTarget)
  tick()
  session.selectHotbar(0)
  assert.deepEqual(tick().block_action.map(action => action.action), ['abort_break'])
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
