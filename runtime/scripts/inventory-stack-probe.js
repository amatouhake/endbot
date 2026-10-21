// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

// Loaded ONLY by raknet_spike --inventory-stack using node --import.
import fs from 'node:fs'
import path from 'node:path'
import { BedrockSession } from '../src/protocol-session.js'
import { createDropTransaction, EMPTY_ITEM } from '../src/packets.js'
import { createStackRequest, stackAction, stackSlot } from '../src/stack-requests.js'

const base = process.env.ENDBOT_INVENTORY_STACK_PROBE
if (!base) throw new Error('Inventory stack probe requires its isolated fixture directory')
const encode = data => JSON.stringify(data, (_, value) => typeof value === 'bigint' ? String(value) : value)
const originalConnect = BedrockSession.prototype.connect
BedrockSession.prototype.connect = async function () {
  await originalConnect.call(this)
  if (this.profile.name !== 'RakNetSpike' || this.stackProbeInstalled) return
  this.stackProbeInstalled = true
  const containers = new Map()
  let lastId
  let nextRequestId = -1
  let current
  const write = () => {
    if (!current) return
    fs.writeFileSync(path.join(base, 'stack-wire.tmp'), encode(current))
    fs.renameSync(path.join(base, 'stack-wire.tmp'), path.join(base, 'stack-wire.json'))
  }
  for (const name of ['inventory_content', 'inventory_slot', 'item_stack_response']) {
    this.client.on(name, packet => {
      if (name === 'inventory_content') containers.set(String(packet.window_id), packet.input.slice())
      if (name === 'inventory_slot') {
        const slots = containers.get(String(packet.window_id)) ?? []
        slots[packet.slot] = packet.item
        containers.set(String(packet.window_id), slots)
      }
      if (current) {
        current.received.push({ name, packet })
        try { write() } catch (error) { current.writeError = error.message }
      }
    })
  }
  const timer = setInterval(() => {
    let request
    try { request = JSON.parse(fs.readFileSync(path.join(base, 'stack-send.json'), 'utf8')) } catch { return }
    if (request.id === lastId) {
      try { write() } catch { /* Retry Windows atomic replacement on the next tick. */ }
      return
    }
    lastId = request.id
    current = { id: request.id, received: [] }
    try {
      const itemAt = slot => {
        const window = { inventory: 'inventory', hotbar: 'inventory', hotbar_and_inventory: 'inventory', armor: 'armor', offhand: 'offhand', cursor: 'ui' }[slot.container]
        // The session has tracked the main inventory since join; this probe
        // only starts listening after spawn and misses the initial content.
        if (window === 'inventory') return this.inventory[slot.slot] ?? EMPTY_ITEM
        return containers.get(window)?.[slot.slot] ?? EMPTY_ITEM
      }
      const reference = slot => {
        const item = itemAt(slot)
        // Zero is valid for an empty destination. Never fabricate the ID of a
        // non-empty item from the read-only hotbar custom event.
        if (Number(item.network_id) !== 0 && !item.has_stack_id) throw new Error('Native stack ID unavailable')
        return stackSlot(slot.container, slot.slot, Number(item.stack_id ?? 0))
      }
      const sourceItem = itemAt(request.source)
      if (!Number(sourceItem.network_id)) throw new Error('Native source item unavailable')
      const count = request.count ?? Number(sourceItem.count)
      let name = 'item_stack_request'
      let packet
      if (request.operation === 'legacy-drop') {
        name = 'inventory_transaction'
        packet = createDropTransaction({ hotbarSlot: request.source.slot, heldItem: sourceItem, stack: count === Number(sourceItem.count) })
      } else {
        const source = reference(request.source)
        const requestId = nextRequestId--
        let actions
        if (request.operation === 'cursor-move') {
          actions = [
            stackAction('take', source, stackSlot('cursor', 0, 0), count),
            stackAction('place', stackSlot('cursor', 0, requestId), reference(request.destination), count)
          ]
        } else {
          actions = [stackAction(request.operation, source, request.destination && reference(request.destination), count)]
        }
        packet = createStackRequest(requestId, actions)
      }
      current.sent = { name, packet, sourceItem }
      this.client.queue(name, packet)
    } catch (error) { current.error = error.message }
    try { write() } catch (error) { console.error(`stack probe write: ${error.message}`) }
  }, 50)
  timer.unref()
  this.client.once('close', () => clearInterval(timer))
}
