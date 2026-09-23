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

test('existing server identity pins retain persistent-artifact validation', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const target = path.join(directory, 'mutable-pin')
  const pin = path.join(directory, 'server.pin')
  fs.writeFileSync(target, 'attacker-controlled-pin\n')
  try {
    fs.symlinkSync(target, pin, process.platform === 'win32' ? 'file' : undefined)
  } catch (error) {
    if (process.platform === 'win32' && ['EPERM', 'EACCES'].includes(error.code)) {
      return t.skip('Creating symlinks requires Windows Developer Mode or elevated privileges')
    }
    throw error
  }
  let createClientCalls = 0
  const session = new BedrockSession(
    { name: 'Alice', identityId: '00000000-0000-4000-8000-000000000001' },
    {
      protocolLoader: async () => ({ createClient: () => { createClientCalls += 1 } }),
      ownerPrivateKeyPath: path.join(directory, 'owner-private.pem'),
      ownerPublicKeyPath: path.join(directory, 'owner-public.pem'),
      serverIdentityPinPath: pin,
      localAuthIssuer: 'endbot-test',
      localAuthAudience: 'endstone-test'
    }
  )

  await assert.rejects(session.connect(), /regular file, not a symlink/)
  assert.equal(createClientCalls, 0)
})

test('corrupt existing server identity pins fail before client creation', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const pin = path.join(directory, 'server.pin')
  fs.writeFileSync(pin, 'not-a-nethernet-identity-pin\n')
  let createClientCalls = 0
  const session = new BedrockSession(
    { name: 'Alice', identityId: '00000000-0000-4000-8000-000000000001' },
    {
      protocolLoader: async () => ({ createClient: () => { createClientCalls += 1 } }),
      ownerPrivateKeyPath: path.join(directory, 'owner-private.pem'),
      ownerPublicKeyPath: path.join(directory, 'owner-public.pem'),
      serverIdentityPinPath: pin,
      localAuthIssuer: 'endbot-test',
      localAuthAudience: 'endstone-test'
    }
  )

  await assert.rejects(session.connect(), /server identity pin is invalid/)
  assert.equal(createClientCalls, 0)
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
  const startInteraction = queued.filter(entry => entry.name === 'player_action').at(-1).packet
  assert.equal(startInteraction.action, 'start_item_use_on')
  assert.deepEqual(startInteraction.position, { x: 3, y: 63, z: 4 })
  assert.equal(startInteraction.face, 1)
  while (!queued.some(entry => entry.name === 'player_auth_input' && entry.packet.transaction)) await nextPacket()
  const interaction = queued.filter(entry => entry.name === 'player_auth_input' && entry.packet.transaction).at(-1).packet
  assert.ok(interaction.input_data.includes('perform_item_interaction'))
  assert.equal(interaction.transaction.data.block_runtime_id, 987)
  assert.equal(interaction.block_action, undefined)
  while (queued.filter(entry => entry.name === 'player_action').length < 2) await nextPacket()
  const interactionActions = queued.filter(entry => entry.name === 'player_action').slice(-2)
  assert.deepEqual(interactionActions.map(entry => entry.packet.action), ['start_item_use_on', 'stop_item_use_on'])

  session.dropSelected(false)
  const drop = queued.filter(entry => entry.name === 'inventory_transaction').at(-1).packet
  assert.equal(drop.transaction.transaction_type, 'normal')
  assert.equal(drop.transaction.actions[0].new_item.count, 0)
  assert.equal(drop.transaction.actions[1].new_item.count, 1)
  await session.disconnect('test complete')
})

test('move-entity delta coordinates update the target used by attack', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const client = fakeLiveClient()
  let resolveAttack
  const attacked = new Promise(resolve => { resolveAttack = resolve })
  client.queue = (name, packet) => {
    if (name === 'inventory_transaction' && packet.transaction.transaction_type === 'item_use_on_entity') {
      resolveAttack(packet.transaction.transaction_data.entity_runtime_id)
    }
  }
  const { session, connecting } = connectedSession(directory, client)
  await turn()
  client.emit('start_game', { player_position: { x: 0, y: 64, z: 0 }, rotation: { x: 0, z: 0 } })
  client.emit('add_entity', {
    runtime_id: 99n,
    unique_id: 100n,
    entity_type: 'minecraft:zombie',
    position: { x: 0, y: 64.62, z: 20 }
  })
  client.emit('move_entity_delta', {
    runtime_entity_id: 99n,
    z: 2,
    on_ground: true,
    force_move: false,
    force_move_local_entity: false,
    force_completion: false,
    ticks: 1n
  })
  const inputs = new InputState()
  inputs.setAction('attack', 'once')
  session.applyInputs(inputs)
  client.emit('spawn')
  await connecting

  assert.equal(await attacked, 99n)
  await session.disconnect('test complete')
})

test('jump once taps and releases instead of holding through the flight', async t => {
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
  const inputs = new InputState()
  inputs.setAction('jump', 'once')
  session.applyInputs(inputs)
  client.emit('spawn')
  await connecting
  for (let tick = 0; tick < 6; tick += 1) await nextTick()

  // The trigger tick presses the key; later airborne ticks must release it so
  // BDS sees a tap. Holding `jumping` through the predicted flight made the
  // server bunny-hop on every landing with no action left for `jump stop`.
  assert.ok(authInputs[0].input_data.includes('jump_down'))
  assert.ok(authInputs[0].input_data.includes('start_jumping'))
  assert.ok(authInputs[0].input_data.includes('jumping'))
  for (const packet of authInputs.slice(1, 6)) {
    assert.equal(packet.input_data.includes('jumping'), false)
    assert.equal(packet.input_data.includes('jump_down'), false)
    assert.equal(packet.input_data.includes('start_jumping'), false)
  }
  // Single ballistic launch: every predicted tick stays above takeoff.
  for (const packet of authInputs.slice(0, 6)) assert.ok(packet.position.y > 64)

  // Authoritative landing must not produce a second jump.
  client.emit('correct_player_move_prediction', {
    prediction_type: 'player',
    position: { x: 0, y: 64, z: 0 },
    delta: { x: 0, y: 0, z: 0 },
    on_ground: true,
    tick: 50n
  })
  for (let tick = 0; tick < 3; tick += 1) await nextTick()
  for (const packet of authInputs.slice(-3)) {
    assert.equal(packet.input_data.includes('jumping'), false)
    assert.equal(packet.input_data.includes('jump_down'), false)
    assert.equal(packet.input_data.includes('start_jumping'), false)
  }
  assert.equal(authInputs.at(-1).position.y, 64)
  assert.ok(authInputs.at(-1).input_data.includes('vertical_collision'))
  await session.disconnect('test complete')
})

test('jump stop clears a continuous hold', async t => {
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
  const inputs = new InputState()
  inputs.setAction('jump', 'continuous')
  session.applyInputs(inputs)
  client.emit('spawn')
  await connecting
  for (let tick = 0; tick < 3; tick += 1) await nextTick()
  for (const packet of authInputs.slice(0, 3)) {
    assert.ok(packet.input_data.includes('jumping'))
  }

  inputs.setAction('jump', 'stop')
  session.applyInputs(inputs)
  for (let tick = 0; tick < 3; tick += 1) await nextTick()
  for (const packet of authInputs.slice(-3)) {
    assert.equal(packet.input_data.includes('jumping'), false)
    assert.equal(packet.input_data.includes('jump_down'), false)
    assert.equal(packet.input_data.includes('start_jumping'), false)
  }
  await session.disconnect('test complete')
})

test('drop predicts selected-slot inventory and re-announces equipment', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const client = fakeLiveClient()
  const queued = []
  client.queue = (name, packet) => queued.push({ name, packet })
  const { session, connecting } = connectedSession(directory, client)
  await turn()
  client.emit('start_game', { player_position: { x: 0, y: 64, z: 0 }, rotation: { x: 0, z: 0 } })
  const items = Array(9).fill(undefined).map(() => ({ ...EMPTY_ITEM }))
  items[0] = { ...usableHeldItem(), count: 5 }
  client.emit('inventory_content', { window_id: 'inventory', input: items })
  client.emit('player_hotbar', { window_id: 'inventory', selected_slot: 0 })
  client.emit('spawn')
  await connecting
  queued.length = 0

  // BDS applies our drop silently, so the session predicts the selected
  // slot exactly like the queued transaction and announces the new held
  // item for observers; otherwise the cache keeps the dropped item and
  // the hand rendering goes stale.
  session.dropSelected(false)
  const drop = queued.filter(entry => entry.name === 'inventory_transaction').at(-1).packet
  assert.equal(drop.transaction.transaction_type, 'normal')
  assert.equal(drop.transaction.actions[0].new_item.count, 4)
  assert.equal(drop.transaction.actions[1].new_item.count, 1)
  assert.equal(session.inventory[0].count, 4)
  const announced = queued.filter(entry => entry.name === 'mob_equipment').at(-1).packet
  assert.equal(announced.selected_slot, 0)
  assert.equal(announced.item.count, 4)

  session.dropSelected(true)
  const dropStack = queued.filter(entry => entry.name === 'inventory_transaction').at(-1).packet
  assert.equal(dropStack.transaction.actions[0].new_item.count, 0)
  assert.equal(dropStack.transaction.actions[1].new_item.count, 4)
  assert.equal(session.inventory[0].network_id, 0)
  const announcedEmpty = queued.filter(entry => entry.name === 'mob_equipment').at(-1).packet
  assert.equal(announcedEmpty.item.network_id, 0)
  await session.disconnect('test complete')
})

test('drop on an empty selected slot fails before sending', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const client = fakeLiveClient()
  const queued = []
  client.queue = (name, packet) => queued.push({ name, packet })
  const { session, connecting } = connectedSession(directory, client)
  await turn()
  client.emit('start_game', { player_position: { x: 0, y: 64, z: 0 }, rotation: { x: 0, z: 0 } })
  session.applyInputs(new InputState())
  client.emit('spawn')
  await connecting
  queued.length = 0

  assert.throws(() => session.dropSelected(false), /Selected hotbar slot is empty/)
  assert.throws(() => session.dropSelected(true), /Selected hotbar slot is empty/)
  assert.equal(queued.filter(entry => entry.name === 'inventory_transaction').length, 0)
  await session.disconnect('test complete')
})

test('strafe left and right follow the facing instead of its mirror', async t => {
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
  // Yaw 0 faces +Z (south). East (+X) is to the left of a south-facing
  // player, so strafing left must move toward +X in both the serialized
  // move vector and the predicted displacement. The key-state flags keep
  // vanilla key semantics: the strafe-left key reports 'left'.
  client.emit('start_game', { player_position: { x: 0, y: 64, z: 0 }, rotation: { x: 0, z: 0 } })
  const inputs = new InputState()
  inputs.setLook(0, 0)
  inputs.setMovement('left')
  session.applyInputs(inputs)
  client.emit('spawn')
  await connecting
  let tick
  while (!queued.some(entry => entry.name === 'player_auth_input' &&
    (entry.packet.move_vector.x !== 0 || entry.packet.move_vector.z !== 0))) await nextPacket()
  tick = queued.filter(entry => entry.name === 'player_auth_input' &&
    (entry.packet.move_vector.x !== 0 || entry.packet.move_vector.z !== 0)).at(-1).packet
  assert.deepEqual(tick.move_vector, { x: 1, z: 0 })
  assert.ok(tick.delta.x > 0)
  assert.equal(tick.delta.z, 0)
  assert.ok(tick.input_data.includes('left'))
  assert.ok(!tick.input_data.includes('right'))

  queued.length = 0
  inputs.setMovement('right')
  while (!queued.some(entry => entry.name === 'player_auth_input' &&
    (entry.packet.move_vector.x !== 0 || entry.packet.move_vector.z !== 0))) await nextPacket()
  tick = queued.filter(entry => entry.name === 'player_auth_input' &&
    (entry.packet.move_vector.x !== 0 || entry.packet.move_vector.z !== 0)).at(-1).packet
  assert.deepEqual(tick.move_vector, { x: -1, z: 0 })
  assert.ok(tick.delta.x < 0)
  assert.equal(tick.delta.z, 0)
  assert.ok(tick.input_data.includes('right'))
  assert.ok(!tick.input_data.includes('left'))
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
