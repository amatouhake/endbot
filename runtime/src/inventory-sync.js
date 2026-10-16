// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import { EMPTY_ITEM } from './packets.js'

export const INVENTORY_EVENT = 'endbot:hotbar-v1'

function nbtTag (tag, depth = 0) {
  if (!tag || typeof tag !== 'object' || depth > 32) throw new Error('Invalid inventory NBT')
  const { type, value } = tag
  if (type === 'compound') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid NBT compound')
    return { type, value: Object.fromEntries(Object.entries(value).map(([key, child]) => [key, nbtTag(child, depth + 1)])) }
  }
  if (type === 'list') {
    const types = ['end', 'byte', 'short', 'int', 'long', 'float', 'double', 'byteArray', 'string', 'list', 'compound', 'intArray']
    if (!value || !types.includes(value.type) || !Array.isArray(value.value) || (value.type === 'end' && value.value.length)) throw new Error('Invalid NBT list')
    return { type, value: { type: value.type, value: value.value.map(child => nbtTag({ type: value.type, value: child }, depth + 1).value) } }
  }
  if (type === 'long') {
    if (typeof value !== 'string' || !/^-?\d+$/.test(value)) throw new Error('Invalid NBT long')
    const number = BigInt(value)
    if (number < -(1n << 63n) || number >= (1n << 63n)) throw new Error('NBT long out of range')
    return { type, value: number }
  }
  const ranges = { byte: [-128, 127], short: [-32768, 32767], int: [-2147483648, 2147483647] }
  if (Object.hasOwn(ranges, type)) {
    if (!Number.isInteger(value) || value < ranges[type][0] || value > ranges[type][1]) throw new Error('Invalid NBT integer')
    return { type, value }
  }
  if (type === 'float' || type === 'double') {
    if (!Number.isFinite(value) || (type === 'float' && Math.abs(value) > 3.4028234663852886e38)) throw new Error('Invalid NBT number')
    return { type, value }
  }
  if (type === 'string') {
    if (typeof value !== 'string') throw new Error('Invalid NBT string')
    return { type, value }
  }
  if (type === 'byteArray' || type === 'intArray') {
    if (!Array.isArray(value)) throw new Error('Invalid NBT array')
    const scalar = type === 'byteArray' ? 'byte' : 'int'
    return { type, value: value.map(number => nbtTag({ type: scalar, value: number }, depth + 1).value) }
  }
  throw new Error('Unsupported inventory NBT')
}

export function decodeHotbar (data, identityId, itemPalette) {
  if (typeof data !== 'string' || Buffer.byteLength(data) > 60 * 1024) throw new Error('Invalid hotbar observation')
  const snapshot = JSON.parse(data)
  if (snapshot.identityId !== identityId || !Array.isArray(snapshot.slots) || snapshot.slots.length !== 9) {
    throw new Error('Hotbar observation does not match this Bot')
  }
  return snapshot.slots.map(slot => {
    if (slot === null) return EMPTY_ITEM
    const networkId = itemPalette.get(slot.name)
    if (!Number.isInteger(networkId) || networkId === 0 || networkId < -32768 || networkId > 32767 ||
        !Number.isInteger(slot.count) || slot.count < 1 || slot.count > 255 ||
        !Number.isSafeInteger(slot.metadata) || slot.metadata < 0 || slot.metadata > 0xffffffff ||
        !Number.isSafeInteger(slot.blockRuntimeId) || slot.blockRuntimeId < 0 || slot.blockRuntimeId > 0xffffffff) {
      throw new Error('Invalid or unknown hotbar item')
    }
    const nbt = nbtTag(slot.nbt)
    if (nbt.type !== 'compound') throw new Error('Invalid item NBT root')
    const stringList = key => {
      const list = nbt.value[key]
      return list?.type === 'list' && list.value.type === 'string' ? list.value.value : []
    }
    const extra = {
      has_nbt: Object.keys(nbt.value).length > 0,
      nbt: Object.keys(nbt.value).length ? { version: 1, nbt: { ...nbt, name: '' } } : undefined,
      can_place_on: stringList('CanPlaceOn'),
      can_destroy: stringList('CanDestroy'),
      ...(slot.name === 'minecraft:shield' ? { blocking_tick: 0n } : {})
    }
    return { network_id: networkId, count: slot.count, metadata: slot.metadata,
      has_stack_id: false, block_runtime_id: slot.blockRuntimeId, extra }
  })
}

export function sameItem (left, right) {
  // Stack network IDs are connection bookkeeping, absent from the read-only
  // SDK observation. Keep native IDs when the actual item is unchanged.
  const comparable = item => {
    const { has_stack_id: unusedPresence, stack_id: unusedId, ...rest } = item ?? EMPTY_ITEM
    return JSON.stringify(rest, (key, value) => typeof value === 'bigint' ? value.toString() : value)
  }
  return comparable(left) === comparable(right)
}
