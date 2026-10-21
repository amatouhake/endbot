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
    await import('../scripts/inventory-stack-probe.js')
    await session.connect()
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
          if (wire.id === value.id) return wire
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
    const legacy = await send({ ...request, id: 'legacy', operation: 'legacy-drop', count: 1 })
    assert.equal(legacy.sent.name, 'inventory_transaction')
    assert.equal(legacy.sent.packet.transaction.actions[0].slot, 9)
    assert.equal(legacy.sent.packet.transaction.actions[0].new_item.count, 4)
  } finally {
    session.client.emit('close')
    if (previous === undefined) delete process.env.ENDBOT_INVENTORY_STACK_PROBE
    else process.env.ENDBOT_INVENTORY_STACK_PROBE = previous
    assert.equal(path.dirname(fs.realpathSync(base)), fs.realpathSync(os.tmpdir()))
    assert.ok(path.basename(base).startsWith('endbot-stack-probe-'))
    fs.rmSync(base, { recursive: true, force: true })
  }
})
