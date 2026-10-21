// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

// Loaded ONLY by raknet_spike --inventory-stack using node --import.
import fs from 'node:fs'
import path from 'node:path'
import { BedrockSession } from '../src/protocol-session.js'
import { createStackRequest, stackAction, stackSlot } from '../src/stack-requests.js'
import { createInventoryClose, createInventoryOpen, createLegacyDrop, createLegacySwap, decodeProbeInventory } from './inventory-stack-builders.js'

const base = process.env.ENDBOT_INVENTORY_STACK_PROBE
if (!base) throw new Error('Inventory stack probe requires its isolated fixture directory')
const encode = data => JSON.stringify(data, (_, value) => typeof value === 'bigint' ? String(value) : value)
const windowKey = id => ({ 0: 'inventory', 119: 'offhand', 120: 'armor', 124: 'ui' })[id] ?? String(id)
const originalConnect = BedrockSession.prototype.connect
BedrockSession.prototype.connect = async function () {
  await originalConnect.call(this)
  if (this.profile.name === 'RakNetSpike' && !this.stackProbeInstalled) installStackProbe(this, base)
}

export function installStackProbe (session, directory, { openWaitMs = 1000, responseWaitMs = 2000 } = {}) {
  session.stackProbeInstalled = true
  const client = session.client
  const containers = new Map()
  let lastId
  // A reconnect must never replay the previous case's command.
  try { lastId = JSON.parse(fs.readFileSync(path.join(directory, 'stack-send.json'), 'utf8')).id } catch {}
  let nextRequestId = -1
  let current
  let request
  let deadline
  const write = () => {
    if (!current) return
    fs.writeFileSync(path.join(directory, 'stack-wire.tmp'), encode(current))
    fs.renameSync(path.join(directory, 'stack-wire.tmp'), path.join(directory, 'stack-wire.json'))
  }
  const persist = () => {
    try { write() } catch (error) { if (current) current.writeError = error.message }
  }
  const record = (name, packet) => {
    if (!current) return
    // Product listeners may later replace slots in the decoded input array.
    // Freeze the recorded wire evidence independently of session state.
    current.received.push({ name, packet: JSON.parse(encode(packet)) })
    if (name === 'disconnect') {
      current.disconnected = true
      current.complete = true
    }
    persist()
  }
  const nativeEvents = ['container_open', 'container_close', 'inventory_content', 'inventory_slot', 'item_stack_response']
  // The generic event precedes normal handlers, including ones that close
  // the client and remove listeners on a disconnect/play_status.
  client.on('packet', decoded => {
    const { name, params } = decoded.data
    if (!nativeEvents.includes(name)) record(name, params)
  })
  for (const name of nativeEvents) {
    client.on(name, packet => {
      if (name === 'inventory_content') containers.set(windowKey(packet.window_id), packet.input.slice())
      if (name === 'inventory_slot') {
        const slots = containers.get(windowKey(packet.window_id)) ?? []
        slots[packet.slot] = packet.item
        containers.set(windowKey(packet.window_id), slots)
      }
      if (current && name === 'container_open') current.open = { ...current.open, answered: true, packet }
      record(name, packet)
    })
  }
  client.on('kick', packet => record('kick', packet))

  const send = () => {
    const itemAt = slot => {
      const window = { inventory: 'inventory', hotbar: 'inventory', hotbar_and_inventory: 'inventory', armor: 'armor', offhand: 'offhand', cursor: 'ui' }[slot.container]
      // Open cases use ONLY packets received since this case's open request.
      // Closed baseline cases may use the session's native inventory history.
      if (!request.openInventory && window === 'inventory') return containers.get(window)?.[slot.slot] ?? session.inventory[slot.slot]
      return containers.get(window)?.[slot.slot]
    }
    const reference = slot => {
      const item = itemAt(slot)
      if (!item) throw new Error('Native slot unavailable')
      if (Number(item.network_id) !== 0 && (!item.has_stack_id || !Number.isInteger(Number(item.stack_id)) || Number(item.stack_id) <= 0)) {
        throw new Error('Native stack ID unavailable')
      }
      return stackSlot(slot.container, slot.slot, Number(item.stack_id ?? 0))
    }
    let sourceItem
    let name = 'item_stack_request'
    let packet
    if (['legacy-drop', 'legacy-swap'].includes(request.operation)) {
      const fixture = JSON.parse(fs.readFileSync(path.join(directory, 'stack-state.json'), 'utf8'))
      if (fixture.id !== request.id) throw new Error('Stale probe inventory observation')
      const observed = decodeProbeInventory(fixture.observation, session.profile.identityId, session.itemPalette)
      sourceItem = request.source.container === 'offhand' ? observed.equipment[0]
        : request.source.container === 'armor' ? observed.equipment[request.source.slot + 1] : observed.slots[request.source.slot]
      name = 'inventory_transaction'
      if (request.operation === 'legacy-swap') {
        const destinationItem = observed.slots[request.destination.slot]
        packet = createLegacySwap({ source: request.source, destination: request.destination, item: sourceItem, destinationItem })
        current.destinationItem = destinationItem
      } else packet = createLegacyDrop({ source: request.source, item: sourceItem, count: request.count ?? Number(sourceItem.count) })
      current.itemOrigin = 'probe-observation-without-stack-ids'
    } else {
      if (request.openInventory && !current.open.answered) throw new Error('BDS did not answer inventory open')
      sourceItem = itemAt(request.source)
      if (!sourceItem || !Number(sourceItem.network_id)) throw new Error('Native source item unavailable')
      const count = request.count ?? Number(sourceItem.count)
      const source = reference(request.source)
      const requestId = nextRequestId--
      const actions = request.operation === 'cursor-move' ? [
        stackAction('take', source, stackSlot('cursor', 0, 0), count),
        stackAction('place', stackSlot('cursor', 0, requestId), reference(request.destination), count)
      ] : [stackAction(request.operation, source, request.destination && reference(request.destination), count)]
      packet = createStackRequest(requestId, actions)
      current.itemOrigin = 'native'
    }
    client.queue(name, packet)
    current.sent = { name, packet, sourceItem }
  }
  const finish = () => {
    if (request.openInventory && current.open?.answered && !current.disconnected) {
      try {
        const packet = createInventoryClose(current.open.packet)
        client.queue('container_close', packet)
        current.close = { name: 'container_close', packet }
      } catch (error) { current.closeError = error.message }
    }
    current.complete = true
    persist()
  }
  const dispatch = () => {
    try { send() } catch (error) { current.error = error.message }
    current.phase = 'response'
    deadline = Date.now() + responseWaitMs
    persist()
  }
  const timer = setInterval(() => {
    if (current && !current.complete) {
      if (Date.now() < deadline) return
      if (current.phase === 'opening') dispatch()
      else finish()
      return
    }
    let incoming
    try { incoming = JSON.parse(fs.readFileSync(path.join(directory, 'stack-send.json'), 'utf8')) } catch { return }
    if (incoming.id === lastId) { persist(); return }
    lastId = incoming.id
    request = incoming
    current = { id: request.id, received: [], complete: false }
    if (request.openInventory) {
      containers.clear()
      current.open = { answered: false }
      try {
        const packet = createInventoryOpen(client.entityId)
        client.queue('interact', packet)
        current.open.sent = { name: 'interact', packet }
        current.phase = 'opening'
        deadline = Date.now() + openWaitMs
      } catch (error) { current.error = error.message; finish() }
      persist()
    } else dispatch()
  }, 50)
  timer.unref()
  client.once('close', reason => {
    clearInterval(timer)
    if (current) {
      current.disconnected = true
      current.complete = true
      record('close', { reason: String(reason ?? 'transport closed without a reason') })
    }
  })
}
