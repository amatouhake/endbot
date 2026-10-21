// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { setTimeout as delay } from 'node:timers/promises'
import { BedrockSession } from '../src/protocol-session.js'
import { EMPTY_ITEM } from '../src/packets.js'

test('opt-in hook sends native IDs, records responses, and never predicts inventory', async () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-stack-probe-'))
  const previous = process.env.ENDBOT_INVENTORY_STACK_PROBE
  process.env.ENDBOT_INVENTORY_STACK_PROBE = base
  const session = new BedrockSession({ name: 'RakNetSpike' }, {})
  session.client = new EventEmitter()
  session.connectPromise = Promise.resolve() // No network, auth or BDS is exercised here.
  const sent = []
  session.client.queue = (name, packet) => sent.push({ name, packet })
  try {
    const { installStackProbe } = await import('../scripts/inventory-stack-probe.js')
    installStackProbe(session, base, { responseWaitMs: 0 })
    const source = { ...EMPTY_ITEM, network_id: 1, count: 5, has_stack_id: true, stack_id: 42 }
    const slots = Array(36).fill(EMPTY_ITEM)
    slots[9] = source
    session.client.emit('inventory_content', { window_id: 'inventory', input: slots })
    const request = { id: 'move', operation: 'place', source: { container: 'inventory', slot: 9 }, destination: { container: 'hotbar', slot: 0 } }
    const send = async value => {
      fs.writeFileSync(path.join(base, 'stack-send.json'), JSON.stringify(value))
      for (let i = 0; i < 100; i++) {
        await delay(20)
        try {
          const wire = JSON.parse(fs.readFileSync(path.join(base, 'stack-wire.json')))
          if (wire.id === value.id && wire.complete) return wire
        } catch {}
      }
      throw new Error('Probe hook did not process the fixture request')
    }
    const wire = await send(request)
    assert.equal(wire.sent.packet.requests[0].actions[0].source.stack_id, 42)
    assert.equal(wire.sent.packet.requests[0].actions[0].destination.stack_id, 0)
    assert.equal(source.count, 5)
    assert.equal(slots[0], EMPTY_ITEM)
    const response = { responses: [{ request_id: -1, status: 'error' }] }
    session.client.emit('item_stack_response', response)
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(base, 'stack-wire.json'))).received, [
      { name: 'item_stack_response', packet: response }
    ])
    // Do not invent an ID for an SDK-only observation; legacy transactions
    // still work without a native stack ID because they use the full item.
    session.client.emit('inventory_slot', { window_id: 'inventory', slot: 9, item: { ...source, has_stack_id: false, stack_id: undefined } })
    assert.equal((await send({ ...request, id: 'missing-id' })).error, 'Native stack ID unavailable')
    assert.equal(sent.length, 1)
    session.profile.identityId = 'probe-bot'
    session.itemPalette.set('minecraft:stone', 1)
    const observation = { identityId: 'probe-bot', slots: Array(36).fill(null), equipment: Array(5).fill(null) }
    observation.slots[9] = { name: 'minecraft:stone', count: 5, metadata: 7, blockRuntimeId: 2, nbt: { type: 'compound', value: {} } }
    fs.writeFileSync(path.join(base, 'stack-state.json'), JSON.stringify({ id: 'legacy', observation }))
    const legacy = await send({ ...request, id: 'legacy', operation: 'legacy-drop', count: 1 })
    assert.equal(legacy.sent.name, 'inventory_transaction')
    assert.equal(legacy.sent.packet.transaction.actions[0].slot, 9)
    assert.equal(legacy.sent.packet.transaction.actions[0].new_item.count, 4)
    assert.equal(legacy.sent.sourceItem.metadata, 7)
    assert.equal(legacy.sent.sourceItem.has_stack_id, false)
    const cachedBefore = structuredClone(session.inventory)
    fs.writeFileSync(path.join(base, 'stack-state.json'), JSON.stringify({ id: 'legacy-swap', observation }))
    const exchange = await send({ ...request, id: 'legacy-swap', operation: 'legacy-swap' })
    assert.equal(exchange.sent.name, 'inventory_transaction')
    assert.equal(exchange.sent.packet.transaction.actions.length, 2)
    assert.equal(exchange.sent.packet.transaction.actions[0].new_item.network_id, 0)
    assert.equal(exchange.sent.packet.transaction.actions[1].new_item.count, 5)
    assert.equal(exchange.destinationItem.network_id, 0)
    assert.equal(exchange.itemOrigin, 'probe-observation-without-stack-ids')
    assert.deepEqual(session.inventory, cachedBefore, 'probe must never predict product inventory')
  } finally {
    session.client.emit('close')
    if (previous === undefined) delete process.env.ENDBOT_INVENTORY_STACK_PROBE
    else process.env.ENDBOT_INVENTORY_STACK_PROBE = previous
    assert.equal(path.dirname(fs.realpathSync(base)), fs.realpathSync(os.tmpdir()))
    assert.ok(path.basename(base).startsWith('endbot-stack-probe-'))
    fs.rmSync(base, { recursive: true, force: true })
  }
})

async function probeFixture (run) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-stack-probe-'))
  const previous = process.env.ENDBOT_INVENTORY_STACK_PROBE
  process.env.ENDBOT_INVENTORY_STACK_PROBE = base
  const session = new BedrockSession({ name: 'RakNetSpike', identityId: 'probe-bot' }, {})
  session.client = new EventEmitter()
  session.client.entityId = 987n
  const sent = []
  session.client.queue = (name, packet) => sent.push({ name, packet })
  const read = () => {
    try { return JSON.parse(fs.readFileSync(path.join(base, 'stack-wire.json'))) } catch { return {} }
  }
  const until = async predicate => {
    for (let i = 0; i < 150; i++) {
      if (predicate()) return
      await delay(10)
    }
    assert.fail('Probe condition timed out')
  }
  const write = request => fs.writeFileSync(path.join(base, 'stack-send.json'), JSON.stringify(request))
  try {
    const { installStackProbe } = await import('../scripts/inventory-stack-probe.js')
    await run({ session, sent, read, until, write, base, installStackProbe })
  } finally {
    session.client.emit('close')
    if (previous === undefined) delete process.env.ENDBOT_INVENTORY_STACK_PROBE
    else process.env.ENDBOT_INVENTORY_STACK_PROBE = previous
    assert.equal(path.dirname(fs.realpathSync(base)), fs.realpathSync(os.tmpdir()))
    assert.ok(path.basename(base).startsWith('endbot-stack-probe-'))
    fs.rmSync(base, { recursive: true, force: true })
  }
}

const openRequest = { id: 'opened', openInventory: true, operation: 'take',
  source: { container: 'inventory', slot: 9 }, destination: { container: 'hotbar', slot: 0 } }

test('open inventory captures native IDs while open and closes the BDS window after response', async () => {
  await probeFixture(async ({ session, sent, read, until, write, installStackProbe, base }) => {
    installStackProbe(session, base, { openWaitMs: 100, responseWaitMs: 100 })
    write(openRequest)
    await until(() => sent.length === 1)
    assert.deepEqual(sent[0], { name: 'interact', packet: { action_id: 'open_inventory', target_entity_id: 987n, has_position: false } })
    const opened = { window_id: 'inventory', window_type: 'inventory' }
    session.client.emit('container_open', opened)
    const slots = Array(36).fill(EMPTY_ITEM)
    slots[9] = { ...EMPTY_ITEM, network_id: 1, count: 5, has_stack_id: true, stack_id: 88 }
    session.client.emit('inventory_content', { window_id: 0, input: slots })
    slots[9] = { ...slots[9], count: 4 }
    assert.equal(read().received.find(event => event.name === 'inventory_content').packet.input[9].count, 5)
    slots[9] = { ...slots[9], count: 5 }
    session.client.emit('inventory_slot', { window_id: 'inventory', slot: 9, item: { ...slots[9], stack_id: 89 } })
    session.client.emit('packet', { data: { name: 'some_followup', params: { value: 1 } } })
    await until(() => Boolean(read().sent))
    const action = read().sent.packet.requests[0].actions[0]
    assert.equal(action.source.stack_id, 89)
    assert.equal(action.source.slot_type.container_id, 'inventory')
    assert.equal(action.destination.slot_type.container_id, 'hotbar')
    session.client.emit('item_stack_response', { responses: [{ request_id: -1, status: 'ok' }] })
    await until(() => read().complete)
    assert.equal(read().open.answered, true)
    assert.deepEqual(sent.at(-1), { name: 'container_close', packet: { ...opened, server: false } })
    assert.ok(read().received.some(event => event.name === 'some_followup'))
    assert.ok(read().received.some(event => event.name === 'item_stack_response'))
  })
})

for (const answer of [false, true]) {
  test(`open inventory missing ${answer ? 'native contents' : 'open answer'} never uses pre-open IDs`, async () => {
    await probeFixture(async ({ session, sent, read, until, write, installStackProbe, base }) => {
      session.inventory[9] = { ...EMPTY_ITEM, network_id: 1, count: 5, has_stack_id: true, stack_id: 123 }
      installStackProbe(session, base, { openWaitMs: 50, responseWaitMs: 0 })
      session.client.emit('inventory_content', { window_id: 'inventory', input: session.inventory })
      write(openRequest)
      await until(() => sent.length === 1)
      if (answer) session.client.emit('container_open', { window_id: 0, window_type: 'inventory' })
      await until(() => read().complete)
      assert.equal(read().open.answered, answer)
      assert.match(read().error, answer ? /Native source item unavailable/ : /did not answer/)
      assert.equal(read().sent, undefined)
      assert.equal(sent.filter(packet => packet.name === 'item_stack_request').length, 0)
      assert.equal(sent.filter(packet => packet.name === 'container_close').length, Number(answer))
    })
  })
}

test('wire captures raw disconnect before listeners are removed, plus play_status, text, kick and transport close', async () => {
  await probeFixture(async ({ session, read, until, write, installStackProbe, base }) => {
    installStackProbe(session, base)
    write({ id: 'diagnostic', operation: 'drop', source: { container: 'armor', slot: 0 } })
    await until(() => read().id === 'diagnostic')
    const raw = (name, params) => session.client.emit('packet', { data: { name, params } })
    raw('play_status', { status: 'failed_client' })
    raw('text', { message: 'kick explanation' })
    const reason = { reason: 12, message: 'inventory error', filtered_message: 'inventory error', hide_disconnect_reason: false }
    raw('disconnect', reason)
    session.client.emit('kick', reason)
    session.client.emit('close')
    session.client.removeAllListeners()
    const wire = read()
    assert.equal(wire.disconnected, true)
    assert.equal(wire.complete, true)
    assert.deepEqual(wire.received.find(event => event.name === 'disconnect').packet, reason)
    assert.deepEqual(wire.received.map(event => event.name), ['play_status', 'text', 'disconnect', 'kick', 'close'])
  })
})

test('reconnected hook ignores the stale command file', async () => {
  await probeFixture(async ({ sent, read, write, installStackProbe, session, base }) => {
    write(openRequest)
    installStackProbe(session, base, { openWaitMs: 0, responseWaitMs: 0 })
    await delay(160)
    assert.equal(sent.length, 0)
    assert.deepEqual(read(), {})
  })
})
