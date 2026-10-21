// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

// Probe-only protocol-2193 builders. No product operation imports this module.
import { decodeHotbar } from '../src/inventory-sync.js'
import { createDropTransaction, EMPTY_ITEM } from '../src/packets.js'
import { stackSlot } from '../src/stack-requests.js'

export function createInventoryOpen (runtimeId) {
  if ((typeof runtimeId !== 'bigint' && !Number.isSafeInteger(runtimeId)) || runtimeId <= 0) {
    throw new Error('Bot runtime entity ID unavailable')
  }
  return { action_id: 'open_inventory', target_entity_id: runtimeId, has_position: false }
}

export function createInventoryClose (opened) {
  if (opened?.window_id === undefined || opened?.window_type === undefined) throw new Error('No BDS-opened window')
  return { window_id: opened.window_id, window_type: opened.window_type, server: false }
}

export function createLegacyDrop ({ source, item, count = Number(item?.count) }) {
  stackSlot(source?.container, source?.slot, 0) // Validate the container and slot.
  const windows = { hotbar_and_inventory: 0, inventory: 0, hotbar: 0, offhand: 119, armor: 120 }
  if (!Object.hasOwn(windows, source.container)) throw new Error('Unsupported legacy source')
  if (!Number.isInteger(count) || count < 1 || count > Number(item?.count)) throw new Error('Invalid legacy drop count')
  const packet = createDropTransaction({ hotbarSlot: source.slot, heldItem: item, stack: true })
  const [container, world] = packet.transaction.actions
  container.window_id = windows[source.container]
  container.new_item = count === Number(item.count) ? EMPTY_ITEM : { ...item, count: Number(item.count) - count }
  world.new_item = { ...item, count }
  return packet
}

export function decodeProbeInventory (snapshot, identityId, palette) {
  if (!snapshot || !Array.isArray(snapshot.slots) || snapshot.slots.length !== 36 ||
      !Array.isArray(snapshot.equipment) || snapshot.equipment.length !== 5) throw new Error('Invalid probe inventory')
  // Reuse the exact product item/NBT decoder in nine-slot batches, without
  // changing the endbot:hotbar-v1 envelope or ever generating stack IDs.
  const decode = slots => decodeHotbar(JSON.stringify({ identityId: snapshot.identityId, slots }), identityId, palette)
  const slots = [0, 9, 18, 27].flatMap(offset => decode(snapshot.slots.slice(offset, offset + 9)))
  const equipment = decode([...snapshot.equipment, null, null, null, null]).slice(0, 5)
  return { slots, equipment }
}
