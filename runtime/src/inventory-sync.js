// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import { isUtf8 } from 'node:buffer'

import { EMPTY_ITEM } from './packets.js'

export const INVENTORY_EVENT = 'endbot:inventory-v2'
export const MAX_INVENTORY_BYTES = 2 * 1024 * 1024

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
    // The observation spells non-finite values out; JSON cannot carry them.
    const special = { NaN, Infinity, '-Infinity': -Infinity }
    if (typeof value === 'string' && Object.hasOwn(special, value)) return { type, value: special[value] }
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

function decodeItem (slot, itemPalette) {
  if (slot === null) return EMPTY_ITEM
  const networkId = itemPalette.get(slot.name)
  if (!Number.isInteger(networkId) || networkId === 0 || networkId < -32768 || networkId > 32767 ||
      !Number.isInteger(slot.count) || slot.count < 1 || slot.count > 255 ||
      !Number.isSafeInteger(slot.metadata) || slot.metadata < 0 || slot.metadata > 0xffffffff ||
      !Number.isSafeInteger(slot.blockRuntimeId) || slot.blockRuntimeId < 0 || slot.blockRuntimeId > 0xffffffff) {
    throw new Error('Invalid or unknown inventory item')
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
}

export function decodeInventory (data, identityId, itemPalette) {
  if (typeof data !== 'string' || Buffer.byteLength(data) > MAX_INVENTORY_BYTES) throw new Error('Invalid inventory observation')
  const snapshot = JSON.parse(data)
  if (snapshot?.identityId !== identityId || !Array.isArray(snapshot.slots) || snapshot.slots.length !== 36 ||
      !Array.isArray(snapshot.equipment) || snapshot.equipment.length !== 5) throw new Error('Invalid inventory identity or shape')
  const decode = items => items.map(item => decodeItem(item, itemPalette))
  return { slots: decode(snapshot.slots), equipment: decode(snapshot.equipment) }
}

export class InventoryObservation {
  constructor () { this.sequence = -1; this.pending = undefined }

  accept (data, identityId, palette) {
    if (typeof data !== 'string' || Buffer.byteLength(data) > 60 * 1024) throw new Error('Invalid inventory fragment')
    const part = JSON.parse(data)
    if (part?.identityId !== identityId || !Number.isSafeInteger(part.sequence) || part.sequence < 0 ||
        !Number.isInteger(part.parts) || part.parts < 1 || part.parts > 64 ||
        !Number.isInteger(part.part) || part.part < 0 || part.part >= part.parts ||
        typeof part.data !== 'string' || part.data.length > 54616 ||
        !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(part.data)) throw new Error('Invalid inventory fragment')
    if (part.sequence <= this.sequence || (this.pending && part.sequence < this.pending.sequence)) return undefined
    if (!this.pending || part.sequence > this.pending.sequence) this.pending = { sequence: part.sequence, parts: part.parts, chunks: new Map(), bytes: 0 }
    const pending = this.pending
    if (pending.parts !== part.parts) throw new Error('Conflicting inventory fragments')
    const chunk = Buffer.from(part.data, 'base64')
    const previous = pending.chunks.get(part.part)
    if (previous && !previous.equals(chunk)) throw new Error('Conflicting inventory fragment')
    if (!previous) { pending.chunks.set(part.part, chunk); pending.bytes += chunk.length }
    if (pending.bytes > MAX_INVENTORY_BYTES) { this.pending = undefined; throw new Error('Inventory exceeds size bound') }
    if (pending.chunks.size !== pending.parts) return undefined
    this.pending = undefined
    const bytes = Buffer.concat(Array.from({ length: pending.parts }, (_, i) => pending.chunks.get(i)))
    if (!isUtf8(bytes)) throw new Error('Invalid inventory UTF-8')
    const snapshot = decodeInventory(bytes.toString('utf8'), identityId, palette)
    this.sequence = part.sequence
    return snapshot
  }
}

// The native NBT decoder yields a long as [high, low] 32-bit words; the
// observation decodes it as BigInt.
const wordPair = value => Array.isArray(value) && value.length === 2 && value.every(Number.isInteger)
const nbtLong = value => wordPair(value) ? BigInt.asIntN(64, (BigInt(value[0]) << 32n) | BigInt(value[1] >>> 0)) : value

// Canonical form for comparing a native item with a synchronized one. The
// protocol decoder yields numbers where the observation uses booleans
// (`has_nbt: 0` vs `false`) and 64-bit values in other shapes, so equal items
// must not differ by representation, key order or absent optional fields.
function canonical (value) {
  if (typeof value === 'bigint' || (typeof value === 'number' && Number.isInteger(value))) return `n:${value}`
  if (Array.isArray(value)) return value.map(canonical)
  if (value?.type === 'long' && wordPair(value.value)) return canonical({ ...value, value: nbtLong(value.value) })
  if (value?.type === 'list' && value.value?.type === 'long' && value.value.value?.some?.(wordPair)) {
    return canonical({ ...value, value: { ...value.value, value: value.value.value.map(nbtLong) } })
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value)
      .filter(([, child]) => child !== undefined)
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([key, child]) => [key, canonical(child)]))
  }
  return value
}

export function sameItem (left, right) {
  // Stack network IDs are connection bookkeeping, absent from the read-only
  // SDK observation. Keep native IDs when the actual item is unchanged. The
  // block runtime ID follows from the item: the observation cannot tell a block
  // item from an item sharing a block's name (wheat, the crop), so it is ignored.
  const comparable = item => {
    const { has_stack_id: unusedPresence, stack_id: unusedId, block_runtime_id: unusedBlock, extra, ...rest } =
      item ?? EMPTY_ITEM
    const normalized = extra && { ...extra, has_nbt: Boolean(extra.has_nbt) }
    return JSON.stringify(canonical({ ...rest, extra: normalized }))
  }
  return comparable(left) === comparable(right)
}
