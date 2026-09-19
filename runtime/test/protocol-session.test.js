// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { InputState } from '../src/actions.js'
import { EMPTY_ITEM } from '../src/packets.js'
import { BedrockSession } from '../src/protocol-session.js'

const turn = () => new Promise(resolve => setImmediate(resolve))

function fakeLiveClient () {
  const client = new EventEmitter()
  client.status = 4
  client.entityId = 7n
  client.queue = () => {}
  client.close = () => queueMicrotask(() => client.emit('close'))
  client.disconnect = () => queueMicrotask(() => client.emit('close'))
  return client
}

function connectedSession (directory, client) {
  const session = new BedrockSession(
    { name: 'Alice', identityId: '00000000-0000-4000-8000-000000000001' },
    {
      protocolLoader: async () => ({ createClient: () => client }),
      ownerPrivateKeyPath: path.join(directory, 'owner-private.pem'),
      ownerPublicKeyPath: path.join(directory, 'owner-public.pem'),
      serverIdentityPinPath: path.join(directory, 'server.pin'),
      localAuthIssuer: 'endbot-test',
      localAuthAudience: 'endstone-test',
      serverHost: 'localhost',
      serverPort: 19132,
      gameVersion: '1.26.51',
      connectTimeoutMs: 1000
    }
  )
  return { session, connecting: session.connect() }
}

test('disconnect waits for paused initialization and prevents late client creation', async () => {
  let releaseProtocol
  let createClientCalls = 0
  const protocolLoaded = new Promise(resolve => { releaseProtocol = resolve })
  const session = new BedrockSession(
    { name: 'Alice', identityId: '00000000-0000-4000-8000-000000000001' },
    {
      protocolLoader: () => protocolLoaded
    }
  )

  const connecting = session.connect()
  await turn()
  const disconnecting = session.disconnect('superseded before client creation')
  let disconnectSettled = false
  void disconnecting.then(() => { disconnectSettled = true })
  await turn()
  assert.equal(disconnectSettled, false)

  releaseProtocol({
    createClient: () => {
      createClientCalls += 1
      return new EventEmitter()
    }
  })

  await assert.rejects(connecting, /connection was canceled/)
  await disconnecting
  assert.equal(createClientCalls, 0)
  assert.equal(session.client, undefined)
})

test('a rejected disconnect can be retried without reusing the rejected promise', async () => {
  const client = new EventEmitter()
  let attempts = 0
  client.disconnect = () => {
    attempts += 1
    if (attempts === 1) throw new Error('first close failed')
    queueMicrotask(() => client.emit('close'))
  }
  client.close = () => {}
  const session = new BedrockSession({ name: 'Alice', identityId: '00000000-0000-4000-8000-000000000001' }, {})
  session.client = client

  await assert.rejects(session.disconnect('first'), /first close failed/)
  await session.disconnect('retry')
  assert.equal(attempts, 2)
})

test('a terminal transport close after disconnect failure makes retry idempotent', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const client = new EventEmitter()
  let attempts = 0
  client.queue = () => {}
  client.disconnect = () => {
    attempts += 1
    throw new Error('first close failed')
  }
  client.close = () => {}
  const { session, connecting } = connectedSession(directory, client)
  await turn()
  client.emit('spawn')
  await connecting

  await assert.rejects(session.disconnect('first'), /first close failed/)
  client.emit('close', 'transport completed closure')
  await session.disconnect('retry after close')

  assert.equal(attempts, 1)
})

test('client errors retain session ownership until transport closure', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const client = new EventEmitter()
  const closeRequests = []
  client.queue = () => {}
  client.disconnect = () => {}
  client.close = reason => closeRequests.push(reason)
  const { session, connecting } = connectedSession(directory, client)
  await turn()
  client.emit('spawn')
  await connecting

  const closures = []
  session.on('close', error => closures.push(error))
  client.emit('error', new Error('recoverable parser failure'))

  assert.deepEqual(closeRequests, ['Endbot protocol error'])
  assert.equal(closures.length, 0)
  client.emit('error', new Error('duplicate parser failure'))
  assert.deepEqual(closeRequests, ['Endbot protocol error'])

  client.emit('close', 'protocol failure shutdown')
  assert.equal(closures.length, 1)
  assert.match(closures[0].message, /recoverable parser failure/)
})

test('start-game rotation seeds the first serialized auth-input tick', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const client = fakeLiveClient()
  let resolveAuthInput
  const authInput = new Promise(resolve => { resolveAuthInput = resolve })
  client.queue = (name, packet) => {
    if (name === 'player_auth_input') resolveAuthInput(packet)
  }
  const { session, connecting } = connectedSession(directory, client)
  await turn()
  client.emit('start_game', {
    player_position: { x: 4, y: 70, z: -3 },
    rotation: { x: -17, z: 231 }
  })
  const inputs = new InputState()
  inputs.setLook(42, 8)
  session.applyInputs(inputs)
  client.emit('spawn')
  await connecting

  const packet = await authInput
  assert.equal(packet.yaw, 231)
  assert.equal(packet.head_yaw, 231)
  assert.equal(packet.pitch, -17)
  assert.deepEqual(inputs.snapshot().look, { yaw: 231, pitch: -17 })
  await session.disconnect('test complete')
})

test('use once releases on the following tick', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const client = fakeLiveClient()
  const transactions = []
  let resolveRelease
  const released = new Promise(resolve => { resolveRelease = resolve })
  client.queue = (name, packet) => {
    if (name !== 'inventory_transaction') return
    transactions.push(packet.transaction.transaction_type)
    if (packet.transaction.transaction_type === 'item_release') resolveRelease()
  }
  const { session, connecting } = connectedSession(directory, client)
  await turn()
  client.emit('start_game', { player_position: { x: 0, y: 64, z: 0 }, rotation: { x: 0, z: 0 } })
  client.emit('inventory_content', { window_id: 'inventory', input: [usableHeldItem()] })
  const inputs = new InputState()
  inputs.setAction('use', 'once')
  session.applyInputs(inputs)
  client.emit('spawn')
  await connecting

  await released
  assert.deepEqual(transactions, ['item_use', 'item_release'])
  await session.disconnect('test complete')
})

test('use stop and global stop release an active continuous use immediately', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const client = fakeLiveClient()
  const transactions = []
  const waiters = []
  client.queue = (name, packet) => {
    if (name !== 'inventory_transaction') return
    transactions.push(packet.transaction.transaction_type)
    waiters.splice(0).forEach(resolve => resolve())
  }
  const nextTransaction = () => new Promise(resolve => waiters.push(resolve))
  const { session, connecting } = connectedSession(directory, client)
  await turn()
  client.emit('start_game', { player_position: { x: 0, y: 64, z: 0 }, rotation: { x: 0, z: 0 } })
  client.emit('inventory_content', { window_id: 'inventory', input: [usableHeldItem()] })
  const inputs = new InputState()
  inputs.setAction('use', 'continuous')
  session.applyInputs(inputs)
  client.emit('spawn')
  await connecting

  await nextTransaction()
  inputs.setAction('use', 'stop')
  session.applyInputs(inputs)
  assert.deepEqual(transactions, ['item_use', 'item_release'])

  inputs.setAction('use', 'continuous')
  await nextTransaction()
  inputs.stopAll()
  session.applyInputs(inputs)
  assert.deepEqual(transactions, ['item_use', 'item_release', 'item_use', 'item_release'])
  await session.disconnect('test complete')
})

test('server teleport acknowledgement starts gravity until authoritative landing', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const client = fakeLiveClient()
  const authInputs = []
  const waiters = []
  client.queue = (name, packet) => {
    if (name !== 'player_auth_input') return
    authInputs.push(packet)
    waiters.splice(0).forEach(resolve => resolve())
  }
  const nextTick = () => new Promise(resolve => waiters.push(resolve))
  const { session, connecting } = connectedSession(directory, client)
  await turn()
  client.emit('start_game', { player_position: { x: 0, y: 64, z: 0 }, rotation: { x: 0, z: 0 } })
  session.applyInputs(new InputState())
  client.emit('spawn')
  await connecting
  await nextTick()
  authInputs.length = 0

  client.emit('move_player', {
    runtime_id: 7n,
    position: { x: 0, y: 90, z: 0 },
    pitch: 0,
    yaw: 0,
    mode: 'teleport',
    on_ground: false,
    tick: 20n
  })
  await nextTick()
  assert.ok(authInputs[0].position.y < 90)
  assert.ok(authInputs[0].input_data.includes('handled_teleport'))
  await nextTick()
  assert.ok(authInputs[1].position.y < authInputs[0].position.y)
  assert.equal(authInputs[1].input_data.includes('handled_teleport'), false)

  client.emit('correct_player_move_prediction', {
    prediction_type: 'player',
    position: { x: 0, y: 64, z: 0 },
    delta: { x: 0, y: 0, z: 0 },
    on_ground: true,
    tick: 30n
  })
  await nextTick()
  assert.equal(authInputs.at(-1).position.y, 64)
  assert.ok(authInputs.at(-1).input_data.includes('vertical_collision'))
  await session.disconnect('test complete')
})

test('hotbar selection interaction and drop use player protocol paths', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const client = fakeLiveClient()
  const queued = []
  const waiters = []
  client.queue = (name, packet) => {
    queued.push({ name, packet })
    waiters.splice(0).forEach(resolve => resolve())
  }
  const nextPacket = () => new Promise(resolve => waiters.push(resolve))
  const { session, connecting } = connectedSession(directory, client)
  await turn()
  client.emit('start_game', { player_position: { x: 1, y: 64, z: 2 }, rotation: { x: 0, z: 0 } })
  const items = Array(9).fill(undefined).map(() => ({ ...EMPTY_ITEM }))
  items[2] = usableHeldItem()
  client.emit('inventory_content', { window_id: 'inventory', input: items })
  session.applyInputs(new InputState())
  client.emit('spawn')
  await connecting

  session.selectHotbar(2)
  assert.equal(session.selectedHotbarSlot(), 2)
  const equipment = queued.find(entry => entry.name === 'mob_equipment')?.packet
  assert.equal(equipment.selected_slot, 2)
  assert.equal(equipment.item.stack_id, 34)

  session.interactBlock({ blockPosition: [3, 63, 4], blockRuntimeId: 987, face: 1 })
  while (!queued.some(entry => entry.name === 'player_auth_input' && entry.packet.transaction)) await nextPacket()
  const interaction = queued.find(entry => entry.name === 'player_auth_input' && entry.packet.transaction).packet
  assert.ok(interaction.input_data.includes('perform_item_interaction'))
  assert.equal(interaction.transaction.data.block_runtime_id, 987)

  session.dropSelected(false)
  const drop = queued.filter(entry => entry.name === 'inventory_transaction').at(-1).packet
  assert.equal(drop.transaction.transaction_type, 'normal')
  assert.equal(drop.transaction.actions[0].new_item.count, 0)
  assert.equal(drop.transaction.actions[1].new_item.count, 1)
  await session.disconnect('test complete')
})

function usableHeldItem () {
  return {
    network_id: 882,
    count: 1,
    metadata: 0,
    has_stack_id: true,
    stack_id: 34,
    block_runtime_id: 0,
    extra: { has_nbt: 0, nbt: undefined, can_place_on: [], can_destroy: [] }
  }
}
