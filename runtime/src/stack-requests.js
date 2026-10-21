// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

// Protocol-2193 spike builders only. Product inventory operations do not use
// these until the locked BDS has been validated with the opt-in live probe.
export function stackSlot (container, slot, stackId) {
  const limits = { hotbar_and_inventory: 35, hotbar: 8, inventory: 35, offhand: 0, armor: 3, cursor: 0 }
  if (!Object.hasOwn(limits, container) || !Number.isInteger(slot) || slot < 0 || slot > limits[container]) {
    throw new Error('Invalid stack request slot')
  }
  if (!Number.isInteger(stackId) || stackId < -2147483648 || stackId > 2147483647) {
    throw new Error('Invalid stack network ID')
  }
  return { slot_type: { container_id: container, dynamic_container_id: undefined }, slot, stack_id: stackId }
}

export function stackAction (type, source, destination, count) {
  const ids = { take: 0, place: 1, swap: 2, drop: 3 }
  if (!Object.hasOwn(ids, type)) throw new Error('Unsupported stack request action')
  if (type !== 'swap' && (!Number.isInteger(count) || count < 1 || count > 255)) {
    throw new Error('Stack request count must be from 1 to 255')
  }
  // Revalidate rather than allowing arbitrary container names through builders.
  const checked = value => stackSlot(value?.slot_type?.container_id, value?.slot, value?.stack_id)
  return {
    type_id: type,
    legacy_type_id: ids[type], // 1.26.40+ repeats the action type as a byte.
    ...(type === 'swap' ? {} : { count }),
    source: checked(source),
    ...(type === 'drop' ? { randomly: false } : { destination: checked(destination) })
  }
}

export function createStackRequest (requestId, actions) {
  if (!Number.isInteger(requestId) || requestId >= 0 || requestId < -2147483648) {
    throw new Error('Use a unique negative stack request ID')
  }
  if (!Array.isArray(actions) || !actions.length) throw new Error('Stack request needs actions')
  return { requests: [{ request_id: requestId, actions, custom_names: [], cause: -1 }] }
}
