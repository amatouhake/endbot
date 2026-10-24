// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

export const EQUIPMENT_LABELS = ['offhand', 'head', 'chest', 'legs', 'feet']
export const MAX_DROP_COUNT = 10455 // 41 slots * protocol item count limit (255).

export function dropPlan ({ selector = 'selected', stack = false, count } = {}, inventory, equipment, selectedSlot, palette) {
  if (typeof stack !== 'boolean') throw new Error('Invalid drop stack flag')
  const entries = [...Array.from({ length: 36 }, (_, slot) => ({
    label: String(slot + 1), windowId: 0, slot, item: inventory[slot]
  })), ...EQUIPMENT_LABELS.map((label, index) => ({
    label, windowId: index === 0 ? 119 : 120, slot: index === 0 ? 0 : index - 1, item: equipment[index]
  }))]
  const itemSelector = typeof selector === 'string' && selector.startsWith('minecraft:')
  const slotSelector = typeof selector === 'string' && /^(?:[1-9]|[12][0-9]|3[0-6])$/.test(selector)
  if (selector !== 'selected' && selector !== 'all' && !slotSelector && !EQUIPMENT_LABELS.includes(selector) && !itemSelector) {
    throw new Error('Invalid drop selector')
  }
  if (count !== undefined && (!itemSelector || !Number.isSafeInteger(count) || count < 1 || count > MAX_DROP_COUNT)) {
    throw new Error('Drop count must be an integer from 1 to 10455')
  }
  if (itemSelector && !/^minecraft:[a-z0-9_./-]+$/.test(selector)) throw new Error('Invalid drop item identifier')
  let budget = count ?? Infinity
  const plan = []
  for (const entry of entries) {
    if (!entry.item?.network_id || !entry.item.count) continue
    if (selector === 'selected' ? entry.label !== String(selectedSlot + 1)
      : itemSelector ? palette.get(selector) !== entry.item.network_id
        : selector !== 'all' && entry.label !== selector) continue
    const amount = Math.min(Number(entry.item.count), selector === 'selected' && !stack ? 1 : budget)
    if (amount <= 0) break
    // The name only labels the reply; an item missing from the palette still drops.
    const itemId = [...palette].find(([, id]) => id === entry.item.network_id)?.[0] ??
      `unknown:${entry.item.network_id}`
    plan.push({ ...entry, itemId, count: amount })
    budget -= amount
  }
  return plan
}
