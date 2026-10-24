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

for (const transport of [undefined, 'raknet']) {
  test(`${transport ?? 'default NetherNet'} preserves local auth and selects only its own connection path`, async t => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-transport-'))
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
    const client = fakeLiveClient()
    let options
    let discoveries = 0
    const session = new BedrockSession(
      { name: 'Alice', identityId: '00000000-0000-4000-8000-000000000001' },
      {
        transport,
        serverHost: '127.0.0.1',
        serverPort: 29432,
        serverName: 'Selected BDS',
        levelName: 'World',
        gameVersion: '1.26.51',
        ownerPrivateKeyPath: path.join(directory, 'owner-private.pem'),
        ownerPublicKeyPath: path.join(directory, 'owner-public.pem'),
        protocolLoader: async () => ({ createClient: value => { options = value; return client } }),
        discover: async () => { discoveries++; return { networkId: 123n } }
      }
    )
    t.after(() => session.disconnect('test cleanup'))
    const connecting = session.connect()
    await turn()
    client.emit('spawn')
    await connecting
    assert.equal(options.transport, transport ?? 'nethernet')
    assert.equal(options.host, '127.0.0.1')
    assert.equal(options.port, 29432)
    assert.equal(options.version, '1.26.51')
    assert.equal(options.offline, false)
    assert.equal(typeof options.authflow.getMinecraftBedrockToken, 'function')
    assert.equal(options.skinData.SelfSignedId, session.profile.identityId)
    assert.equal(options.skipPing, true)
    if (transport === 'raknet') {
      assert.equal(discoveries, 0)
      assert.equal('nethernet' in options, false)
      assert.equal(options.followPort, false)
      assert.equal(options.raknetBackend, 'raknet-native')
    } else {
      assert.equal(discoveries, 1)
      assert.deepEqual(options.nethernet, { signalling: 'lan', networkId: 123n })
    }
  })
}

test('sessions reject an unknown transport before loading protocol code', async () => {
  const session = new BedrockSession({}, {
    transport: 'auto',
    serverHost: '127.0.0.1',
    protocolLoader: () => { throw new Error('must not load') }
  })
  await assert.rejects(session.connect(), /transport must/)
})

test('disconnect waits for paused initialization and prevents late client creation', async () => {
  let releaseProtocol
  let createClientCalls = 0
  const protocolLoaded = new Promise(resolve => { releaseProtocol = resolve })
  const session = new BedrockSession(
    { name: 'Alice', identityId: '00000000-0000-4000-8000-000000000001' },
    {
      protocolLoader: () => protocolLoaded,
      serverHost: '127.0.0.1'
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

test('radius changed during initialization controls the first upstream request', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-radius-connect-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  let release
  const loading = new Promise(resolve => { release = resolve })
  const client = fakeLiveClient()
  const session = new BedrockSession(
    { name: 'Alice', identityId: '00000000-0000-4000-8000-000000000001' },
    { serverHost: 'localhost', chunkRadius: 5, protocolLoader: () => loading,
      ownerPrivateKeyPath: path.join(directory, 'owner-private.pem'), ownerPublicKeyPath: path.join(directory, 'owner-public.pem') }
  )
  t.after(() => session.disconnect('cleanup'))
  const connecting = session.connect()
  session.setChunkRadius(12)
  release({ createClient: () => client })
  await turn()
  assert.equal(client.viewDistance, 12)
  client.emit('spawn')
  await connecting
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

test('non-loopback servers are refused before client creation', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  for (const serverHost of ['192.0.2.10', 'example.com', '0.0.0.0']) {
    let createClientCalls = 0
    const session = new BedrockSession(
      { name: 'Alice', identityId: '00000000-0000-4000-8000-000000000001' },
      {
        protocolLoader: async () => ({ createClient: () => { createClientCalls += 1 } }),
        ownerPrivateKeyPath: path.join(directory, 'owner-private.pem'),
        ownerPublicKeyPath: path.join(directory, 'owner-public.pem'),
        serverHost,
        localAuthIssuer: 'endbot-test',
        localAuthAudience: 'endstone-test'
      }
    )

    await assert.rejects(session.connect(), /loopback/)
    assert.equal(createClientCalls, 0)
  }
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
    if (name === 'player_auth_input' && packet.transaction) transactions.push('item_use')
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
    if (name === 'player_auth_input' && packet.transaction?.data.trigger_type === 'player_input') {
      transactions.push('item_use')
    } else if (name === 'inventory_transaction') {
      transactions.push(packet.transaction.transaction_type)
    } else return
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
  t.after(() => session.disconnect('test cleanup'))
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
  const equipment = queued.findLast(entry => entry.name === 'mob_equipment')?.packet
  assert.equal(equipment.selected_slot, 2)
  assert.equal(equipment.item.stack_id, 34)

  session.interactBlock({ blockPosition: [3, 63, 4], blockRuntimeId: 987, face: 1 })
  const startInteraction = queued.filter(entry => entry.name === 'player_action').at(-1).packet
  assert.equal(startInteraction.action, 'start_item_use_on')
  assert.deepEqual(startInteraction.position, { x: 3, y: 63, z: 4 })
  assert.equal(startInteraction.face, 1)
  while (!queued.some(entry => entry.name === 'player_auth_input' && entry.packet.transaction)) await nextPacket()
  const interaction = queued.filter(entry => entry.name === 'player_auth_input' && entry.packet.transaction).at(-1).packet
  assert.ok(interaction.input_data.includes('item_interact'))
  assert.equal(interaction.transaction.data.block_runtime_id, 987)
  assert.equal(interaction.block_action, undefined)
  while (queued.filter(entry => entry.name === 'player_action').length < 2) await nextPacket()
  const interactionActions = queued.filter(entry => entry.name === 'player_action').slice(-2)
  assert.deepEqual(interactionActions.map(entry => entry.packet.action), ['start_item_use_on', 'stop_item_use_on'])

  session.itemPalette.set('minecraft:test_item', session.heldItem.network_id)
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
  session.itemPalette.set('minecraft:test_item', session.heldItem.network_id)
  session.dropSelected(false)
  const drop = queued.filter(entry => entry.name === 'inventory_transaction').at(-1).packet
  assert.equal(drop.transaction.transaction_type, 'normal')
  assert.equal(drop.transaction.actions[0].new_item.count, 4)
  assert.equal(drop.transaction.actions[1].new_item.count, 1)
  assert.equal(session.inventory[0].count, 4)
  const announced = queued.filter(entry => entry.name === 'mob_equipment').at(-1).packet
  assert.equal(announced.selected_slot, 0)
  assert.equal(announced.item.count, 4)

  session.itemPalette.set('minecraft:test_item', session.heldItem.network_id)
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

async function entitySession (t, queued) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const client = fakeLiveClient()
  client.queue = (name, packet) => queued.push({ name, packet })
  const { session, connecting } = connectedSession(directory, client)
  await turn()
  client.emit('start_game', { entity_id: -5n, player_position: { x: 0, y: 64, z: 0 }, rotation: { x: 0, z: 0 } })
  const inputs = new InputState()
  session.applyInputs(inputs)
  client.emit('spawn')
  await connecting
  t.after(() => session.disconnect('test complete'))
  const add = (runtimeId, type, position) => client.emit('add_entity', {
    runtime_id: runtimeId, unique_id: -runtimeId, entity_type: type, position
  })
  return { session, client, inputs, add }
}

const nextTick = () => new Promise(resolve => setTimeout(resolve, 80))

test('nearby entities exclude players and stay sorted and bounded', async t => {
  const { session, client, add } = await entitySession(t, [])
  add(20n, 'minecraft:cow', { x: 0, y: 64, z: 5 })
  add(21n, 'minecraft:sheep', { x: 0, y: 64, z: 2 })
  add(22n, 'minecraft:zombie', { x: 0, y: 64, z: 40 })
  client.emit('add_player', { runtime_id: 23n, unique_id: -23n, position: { x: 0, y: 64, z: 1 } })
  const entities = session.nearbyEntities()
  assert.deepEqual(entities.map(entity => [entity.runtimeId, entity.type]),
    [['21', 'minecraft:sheep'], ['20', 'minecraft:cow']])
})

test('interact entity turns toward the nearest match and right-clicks it on the next tick', async t => {
  const queued = []
  const { session, inputs, add } = await entitySession(t, queued)
  add(20n, 'minecraft:cow', { x: 3, y: 64, z: 0 })
  add(21n, 'minecraft:cow', { x: 0, y: 64, z: 5 })
  add(22n, 'minecraft:sheep', { x: 0, y: 64, z: 1 })
  const target = session.interactEntity({ type: 'cow' })
  assert.equal(target.runtimeId, '20')
  assert.ok(Math.abs(inputs.snapshot().look.yaw - 270) < 0.01)
  await nextTick()
  const transaction = queued.find(entry => entry.name === 'inventory_transaction')
  assert.equal(transaction.packet.transaction.transaction_type, 'item_use_on_entity')
  assert.equal(transaction.packet.transaction.transaction_data.action_type, 'interact')
  assert.equal(transaction.packet.transaction.transaction_data.entity_runtime_id, 20n)
  const order = queued.map(entry => entry.name)
  assert.ok(order.indexOf('player_auth_input') < order.indexOf('inventory_transaction'))
  assert.equal(session.pendingEntityInteraction, undefined)
})

test('interact entity rejects players, unknown IDs and targets out of reach', async t => {
  const { session, client, add } = await entitySession(t, [])
  add(20n, 'minecraft:cow', { x: 0, y: 64, z: 12 })
  client.emit('add_player', { runtime_id: 23n, unique_id: -23n, position: { x: 0, y: 64, z: 1 } })
  assert.throws(() => session.interactEntity({ type: 'cow' }), /No matching entity within reach/)
  assert.throws(() => session.interactEntity({}), /No entity within reach/)
  assert.throws(() => session.interactEntity({ runtimeId: '23' }), /No entity with that ID/)
  assert.throws(() => session.interactEntity({ runtimeId: '20' }), /out of reach/)
})

test('riding follows the vehicle until BDS removes the link, and dismount leaves it', async t => {
  const queued = []
  const { session, client, add } = await entitySession(t, queued)
  add(30n, 'minecraft:minecart', { x: 0, y: 64, z: 2 })
  client.emit('set_entity_link', { link: { ridden_entity_id: -30n, rider_entity_id: -5n, type: 1 } })
  assert.deepEqual(session.vehicleStatus(), { type: 'minecraft:minecart', runtimeId: '30' })
  client.emit('move_entity', { runtime_entity_id: 30n, position: { x: 0, y: 64, z: 12 } })
  await nextTick()
  const last = queued.filter(entry => entry.name === 'player_auth_input').at(-1)
  assert.equal(last.packet.position.z, 12)
  // Eyes as if standing on the block the vehicle is in, not the boarding height.
  assert.equal(last.packet.position.y, 64 + 1.62)
  assert.deepEqual(session.dismount(), { type: 'minecraft:minecart', runtimeId: '30' })
  const leave = queued.find(entry => entry.name === 'interact' && entry.packet.action_id === 'leave_vehicle')
  assert.equal(leave.packet.target_entity_id, 30n)
  client.emit('set_entity_link', { link: { ridden_entity_id: -30n, rider_entity_id: -5n, type: 0 } })
  assert.equal(session.vehicleStatus(), null)
  assert.throws(() => session.dismount(), /not riding/)
})

test('links for other riders and a removed vehicle do not leave stale riding state', async t => {
  const { session, client, add } = await entitySession(t, [])
  add(30n, 'minecraft:minecart', { x: 0, y: 64, z: 2 })
  client.emit('set_entity_link', { link: { ridden_entity_id: -30n, rider_entity_id: -99n, type: 1 } })
  assert.equal(session.vehicleStatus(), null)
  client.emit('set_entity_link', { link: { ridden_entity_id: -30n, rider_entity_id: -5n, type: 1 } })
  client.emit('remove_entity', { entity_id_self: -30n })
  assert.equal(session.vehicleStatus(), null)
})

test('a rider can switch vehicles and keeps a repeated link', async t => {
  const { session, client, add } = await entitySession(t, [])
  add(30n, 'minecraft:minecart', { x: 0, y: 64, z: 2 })
  add(31n, 'minecraft:boat', { x: 3, y: 64, z: 2 })
  client.emit('set_entity_link', { link: { ridden_entity_id: -30n, rider_entity_id: -5n, type: 1 } })
  client.emit('move_entity', { runtime_entity_id: 30n, position: { x: 0, y: 64, z: 3 } })
  // The nearest target skips the vehicle the Bot is riding.
  assert.equal(session.interactEntity({}).runtimeId, '31')
  client.emit('set_entity_link', { link: { ridden_entity_id: -31n, rider_entity_id: -5n, type: 1 } })
  client.emit('set_entity_link', { link: { ridden_entity_id: -30n, rider_entity_id: -5n, type: 0 } })
  assert.deepEqual(session.vehicleStatus(), { type: 'minecraft:boat', runtimeId: '31' })
  const seat = session.vehicle
  client.emit('set_entity_link', { link: { ridden_entity_id: -31n, rider_entity_id: -5n, type: 1 } })
  assert.equal(session.vehicle, seat)
})

test('dismount reports the exit spot while riding, then leaves and stays there', async t => {
  const queued = []
  const { session, client, add } = await entitySession(t, queued)
  add(31n, 'minecraft:boat', { x: 0, y: 64, z: 2 })
  client.emit('set_entity_link', { link: { ridden_entity_id: -31n, rider_entity_id: -5n, type: 1 } })
  assert.throws(() => session.dismount({ x: 9, y: 64, z: 2 }), /too far/)
  session.dismount({ x: 1.5, y: 64, z: 2.5 })
  assert.throws(() => session.dismount(), /already pending/)
  assert.equal(queued.some(entry => entry.packet?.action_id === 'leave_vehicle'), false)
  await nextTick()
  await nextTick()
  const inputs = queued.filter(entry => entry.name === 'player_auth_input')
  const leave = queued.findIndex(entry => entry.packet?.action_id === 'leave_vehicle')
  assert.ok(leave > 0)
  assert.equal(queued[leave].packet.target_entity_id, 31n)
  const before = queued.slice(0, leave).filter(entry => entry.name === 'player_auth_input').at(-1)
  assert.deepEqual(before.packet.position, { x: 1.5, y: 65.62, z: 2.5 })
  assert.ok(inputs.length >= 2)
  client.emit('set_entity_link', { link: { ridden_entity_id: -31n, rider_entity_id: -5n, type: 0 } })
  assert.equal(session.vehicleStatus(), null)
  assert.deepEqual(session.position, { x: 1.5, y: 65.62, z: 2.5 })
})

test('own hunger and saturation come from update_attributes; other entities are ignored', async t => {
  const { session, client } = await entitySession(t, [])
  assert.deepEqual(session.vitals(), { hunger: null, saturation: null })
  client.emit('update_attributes', { runtime_entity_id: 99n, attributes: [{ name: 'minecraft:player.hunger', current: 3 }] })
  client.emit('update_attributes', { runtime_entity_id: 7n, attributes: [
    { name: 'minecraft:player.hunger', current: 17 }, { name: 'minecraft:player.saturation', current: 4.5 },
    { name: 'minecraft:health', current: 20 }] })
  assert.deepEqual(session.vitals(), { hunger: 17, saturation: 4.5 })
})

test('nearby entities filter by id or type before the list limit', async t => {
  const { session, add } = await entitySession(t, [])
  for (let index = 0; index < 12; index++) add(BigInt(40 + index), 'minecraft:sheep', { x: index, y: 64, z: 1 })
  add(60n, 'minecraft:cow', { x: 0, y: 64, z: 15 })
  assert.equal(session.nearbyEntities().length, 10)
  assert.deepEqual(session.nearbyEntities({ type: 'cow' }).map(entity => entity.runtimeId), ['60'])
  assert.deepEqual(session.nearbyEntities({ runtimeId: '51' }).map(entity => entity.type), ['minecraft:sheep'])
})

test('a plugin unsupported notice starts a fall only for this Bot on the ground and not riding', async t => {
  const { session, client, add } = await entitySession(t, [])
  const notice = identityId => client.emit('script_custom_event', {
    event_name: 'endbot:unsupported-v1', event_data: JSON.stringify({ identityId })
  })
  session.onGround = true
  notice('00000000-0000-4000-8000-000000000099')
  assert.equal(session.onGround, true)
  notice(session.profile.identityId)
  assert.equal(session.onGround, false)
  session.onGround = true
  add(31n, 'minecraft:boat', { x: 0, y: 64, z: 2 })
  client.emit('set_entity_link', { link: { ridden_entity_id: -31n, rider_entity_id: -5n, type: 1 } })
  notice(session.profile.identityId)
  assert.equal(session.onGround, true)
})

test('attack @selector turns to the entity and left-clicks it within melee reach', async t => {
  const queued = []
  const { session, add } = await entitySession(t, queued)
  add(70n, 'minecraft:zombie', { x: 0, y: 64, z: 3 })
  add(71n, 'minecraft:zombie', { x: 0, y: 64, z: 5.5 })
  assert.throws(() => session.attackEntity({ runtimeId: '71' }), /out of reach/)
  assert.equal(session.attackEntity({ type: 'zombie' }).runtimeId, '70')
  await nextTick()
  const transaction = queued.find(entry => entry.name === 'inventory_transaction')
  assert.equal(transaction.packet.transaction.transaction_data.action_type, 'attack')
  assert.equal(transaction.packet.transaction.transaction_data.entity_runtime_id, 70n)
})

test('a counted targeted interact alternates between matches and stops after the count', async t => {
  const queued = []
  const { session, inputs, add } = await entitySession(t, queued)
  add(80n, 'minecraft:cow', { x: 0, y: 64, z: 2 })
  add(81n, 'minecraft:cow', { x: 0, y: 64, z: 3 })
  inputs.setAction('interact', 'interval', 2, undefined, undefined, { type: 'minecraft:cow' }, 3)
  session.applyInputs(inputs)
  await new Promise(resolve => setTimeout(resolve, 1400))
  const clicks = queued.filter(entry => entry.name === 'inventory_transaction' &&
    entry.packet.transaction.transaction_type === 'item_use_on_entity')
    .map(entry => String(entry.packet.transaction.transaction_data.entity_runtime_id))
  assert.deepEqual(clicks, ['80', '81', '80'])
  assert.equal(inputs.snapshot().actions.interact, undefined)
})

test('a counted targeted interact clicks a different entity each time while fresh ones are in reach', async t => {
  const queued = []
  const { session, inputs, add } = await entitySession(t, queued)
  add(80n, 'minecraft:cow', { x: 0, y: 64, z: 2 })
  add(81n, 'minecraft:cow', { x: 0, y: 64, z: 3 })
  add(82n, 'minecraft:cow', { x: 0, y: 64, z: 4 })
  inputs.setAction('interact', 'continuous', undefined, undefined, undefined, { type: 'minecraft:cow' }, 3)
  session.applyInputs(inputs)
  await new Promise(resolve => setTimeout(resolve, 1400))
  const clicks = queued.filter(entry => entry.name === 'inventory_transaction' &&
    entry.packet.transaction.transaction_type === 'item_use_on_entity')
    .map(entry => String(entry.packet.transaction.transaction_data.entity_runtime_id))
  assert.deepEqual(clicks, ['80', '81', '82'])
})

test('a repeated entity click waits for BDS to report the held item it changed', async t => {
  const queued = []
  const { session, inputs, client, add } = await entitySession(t, queued)
  const clicks = () => queued.filter(entry => entry.packet?.transaction?.transaction_type === 'item_use_on_entity').length
  add(80n, 'minecraft:cow', { x: 0, y: 64, z: 2 })
  add(81n, 'minecraft:cow', { x: 0, y: 64, z: 3 })
  inputs.setAction('interact', 'continuous', undefined, undefined, undefined, { type: 'minecraft:cow' }, 2)
  session.applyInputs(inputs)
  await new Promise(resolve => setTimeout(resolve, 200))
  assert.equal(clicks(), 1)  // the stale held item would make BDS reject a second click now
  client.emit('inventory_slot', { window_id: 'inventory', slot: 0, item: { network_id: 7, count: 4, metadata: 0 } })
  await new Promise(resolve => setTimeout(resolve, 150))
  assert.equal(clicks(), 2)
})

test('a repeated targeted click carries its own aim in the same tick input', async t => {
  const queued = []
  const { session, inputs, add } = await entitySession(t, queued)
  add(80n, 'minecraft:cow', { x: 2, y: 64, z: 0 })
  inputs.setAction('interact', 'continuous', undefined, undefined, undefined, { type: 'minecraft:cow' }, 1)
  session.applyInputs(inputs)
  await new Promise(resolve => setTimeout(resolve, 200))
  const click = queued.findIndex(entry => entry.packet?.transaction?.transaction_type === 'item_use_on_entity')
  const input = queued.slice(0, click).findLast(entry => entry.name === 'player_auth_input')
  assert.ok(Math.abs(((input.packet.yaw % 360) + 360) % 360 - 270) < 1, `yaw ${input.packet.yaw}`)
})

test('a targeted attack with nothing in reach never falls back to the crosshair', async t => {
  const queued = []
  const { session, client, inputs } = await entitySession(t, queued)
  client.emit('add_player', { runtime_id: 23n, unique_id: -23n, position: { x: 0, y: 64, z: 1.5 } })
  client.emit('add_entity', { runtime_id: 24n, unique_id: -24n, entity_type: 'minecraft:cow', position: { x: 0, y: 64, z: 2.5 } })
  inputs.setAction('attack', 'continuous', undefined, undefined, undefined, { type: 'minecraft:zombie' })
  session.applyInputs(inputs)
  await new Promise(resolve => setTimeout(resolve, 300))
  assert.equal(queued.some(entry => entry.name === 'inventory_transaction'), false)
  assert.equal(queued.some(entry => entry.name === 'animate'), false)
})

test('a dimension change ends targeted actions but keeps untargeted ones', async t => {
  const { session, client, inputs } = await entitySession(t, [])
  inputs.setAction('attack', 'continuous', undefined, undefined, undefined, { type: 'minecraft:zombie' })
  inputs.setAction('jump', 'continuous')
  session.applyInputs(inputs)
  client.emit('change_dimension', { dimension: 1, position: { x: 0, y: 64, z: 0 }, respawn: false })
  assert.equal(inputs.snapshot().actions.attack, undefined)
  assert.equal(inputs.snapshot().actions.jump.mode, 'continuous')
})

test('targeted attack continuous re-aims and skips triggers with nothing in reach', async t => {
  const queued = []
  const { session, inputs, client, add } = await entitySession(t, queued)
  inputs.setAction('attack', 'interval', 2, 10, undefined, { type: 'minecraft:zombie' })
  session.applyInputs(inputs)
  await new Promise(resolve => setTimeout(resolve, 200))
  assert.equal(queued.some(entry => entry.packet?.transaction?.transaction_type === 'item_use_on_entity'), false)
  add(90n, 'minecraft:zombie', { x: 0, y: 64, z: 2 })
  await new Promise(resolve => setTimeout(resolve, 300))
  const attack = queued.find(entry => entry.packet?.transaction?.transaction_type === 'item_use_on_entity')
  assert.equal(attack.packet.transaction.transaction_data.action_type, 'attack')
  assert.throws(() => inputs.setAction('interact', 'continuous'), /entity target/)
  assert.throws(() => inputs.setAction('jump', 'continuous', undefined, undefined, undefined, { type: 'cow' }), /attack and interact/)
  assert.throws(() => inputs.setAction('attack', 'once', undefined, undefined, undefined, { type: 'cow' }, 2), /Count/)
  client.emit('remove_entity', { entity_id_self: -90n })
})

test('a dismount BDS never confirms stops counting as riding, and @nearest skips drops', async t => {
  const queued = []
  const { session, client, add } = await entitySession(t, queued)
  add(60n, 'minecraft:boat', { x: 0, y: 64, z: 1 })
  add(61n, 'minecraft:item', { x: 0, y: 64, z: 0.5 })
  add(62n, 'minecraft:cow', { x: 0, y: 64, z: 3 })
  assert.equal(session.nearbyEntities()[0].type, 'minecraft:item')  // listed, but never the nearest target
  assert.equal(session.interactEntity({}).type, 'minecraft:boat')
  await nextTick()
  client.emit('set_entity_link', { link: { type: 1, ridden_entity_id: -60n, rider_entity_id: -5n } })
  assert.equal(session.vehicleStatus().type, 'minecraft:boat')
  session.dismount()
  assert.throws(() => session.dismount(), /already pending/)
  assert.equal(queued.filter(entry => entry.name === 'interact' && entry.packet.action_id === 'leave_vehicle').length, 1)
  await new Promise(resolve => setTimeout(resolve, 3200))
  assert.equal(session.vehicleStatus(), null)
})

test('drop all before BDS sent the inventory is an error, not an empty success', async t => {
  const { session } = await entitySession(t, [])
  assert.throws(() => session.drop({ selector: 'all' }), /not synchronized/)
})
