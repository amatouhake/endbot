// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

export const EMPTY_ITEM = { network_id: 0, count: 0, metadata: 0, has_stack_id: false, block_runtime_id: 0 }

export function hasUsableHeldItem (item) {
  return item != null && Number(item.network_id) !== 0
}

export function serverRotation (packet) {
  const yaw = packet?.yaw ?? packet?.rotation?.z
  const pitch = packet?.pitch ?? packet?.rotation?.x
  if (!Number.isFinite(yaw) || !Number.isFinite(pitch)) return undefined
  return { yaw, pitch }
}

export function addJumpInputFlags (inputData, { started, airborne }) {
  if (started) inputData.push('jump_down', 'start_jumping')
  if (airborne) inputData.push('jumping')
}

export function addToggleInputFlags (inputData, state) {
  if (state.sprint) inputData.push('sprinting')
  // BDS 1.26 keys sustained sneak on the per-tick raw flags, not just the
  // cooked `sneaking` steady flag: without held `sneak_current_raw` the
  // server drops the crouch one tick after the press edge. Vanilla asserts
  // pressed+current on the press tick, current while held, released on
  // release; mirror exactly that sequence and nothing else.
  if (state.sneak) inputData.push('sneaking', 'sneak_current_raw')
  const names = {
    start_sprint: 'start_sprinting',
    stop_sprint: 'stop_sprinting',
    start_sneak: 'start_sneaking',
    stop_sneak: 'stop_sneaking'
  }
  for (const transition of state.transitions) {
    // Transitions fire once, so press/release edges are sent only on their
    // own tick, never repeated while held.
    if (transition === 'start_sneak') inputData.push('sneak_down', 'sneak_pressed_raw')
    if (transition === 'stop_sneak') inputData.push('sneak_released_raw')
    inputData.push(names[transition])
  }
}

export function createUseTransaction ({ hotbarSlot, heldItem, position }) {
  return {
    transaction: {
      legacy: { legacy_request_id: 0 },
      transaction_type: 'item_use',
      actions: [],
      transaction_data: {
        action_type: 'click_air',
        trigger_type: 'player_input',
        block_position: {
          x: Math.floor(position.x),
          y: Math.floor(position.y),
          z: Math.floor(position.z)
        },
        face: 255,
        hotbar_slot: hotbarSlot,
        hand: 'main_hand',
        held_item: heldItem,
        player_pos: { ...position },
        click_pos: { x: 0, y: 0, z: 0 },
        block_runtime_id: 0,
        client_prediction: 'success',
        client_cooldown_state: 'off'
      }
    }
  }
}

export function createBlockInteractionInput ({ hotbarSlot, heldItem, position, blockPosition, blockRuntimeId, face }) {
  const click = { x: 0.5, y: 0.5, z: 0.5 }
  if (face === 0) click.y = 0
  if (face === 1) click.y = 1
  if (face === 2) click.z = 0
  if (face === 3) click.z = 1
  if (face === 4) click.x = 0
  if (face === 5) click.x = 1
  return {
    legacy: { legacy_request_id: 0 },
    actions: [],
    data: {
      action_type: 'click_block',
      trigger_type: 'player_input',
      block_position: { x: blockPosition[0], y: blockPosition[1], z: blockPosition[2] },
      face,
      hotbar_slot: hotbarSlot,
      hand: 'main_hand',
      held_item: heldItem,
      player_pos: { ...position },
      click_pos: click,
      block_runtime_id: blockRuntimeId,
      client_prediction: 'success',
      client_cooldown_state: 'off'
    }
  }
}

export function createBlockInteractionAction ({ runtimeEntityId, blockPosition, face, action }) {
  return {
    runtime_entity_id: runtimeEntityId,
    action,
    position: { x: blockPosition[0], y: blockPosition[1], z: blockPosition[2] },
    result_position: { x: blockPosition[0], y: blockPosition[1], z: blockPosition[2] },
    face
  }
}

function withCount (item, count) {
  return count === 0 ? EMPTY_ITEM : { ...item, count }
}

export function createDropTransaction ({ hotbarSlot, heldItem, stack }) {
  if (!hasUsableHeldItem(heldItem)) throw new Error('Selected hotbar slot is empty')
  const count = stack ? Number(heldItem.count) : 1
  const remaining = Number(heldItem.count) - count
  return {
    transaction: {
      legacy: { legacy_request_id: 0 },
      transaction_type: 'normal',
      actions: [
        {
          source_type: 'container',
          window_id: 0,
          flags: undefined,
          slot: hotbarSlot,
          old_item: heldItem,
          new_item: withCount(heldItem, remaining)
        },
        {
          source_type: 'world_interaction',
          window_id: undefined,
          flags: 0,
          slot: 0,
          old_item: EMPTY_ITEM,
          new_item: withCount(heldItem, count)
        }
      ],
      transaction_data: undefined
    }
  }
}

export function createReleaseTransaction ({ hotbarSlot, heldItem, position }) {
  return {
    transaction: {
      legacy: { legacy_request_id: 0 },
      transaction_type: 'item_release',
      actions: [],
      transaction_data: {
        action_type: 'release',
        hotbar_slot: hotbarSlot,
        held_item: heldItem,
        head_pos: { x: position.x, y: position.y + 1.62, z: position.z }
      }
    }
  }
}

export function createAttackTransaction ({ runtimeId, hotbarSlot, heldItem, position, clickPosition }) {
  return {
    transaction: {
      legacy: { legacy_request_id: 0 },
      transaction_type: 'item_use_on_entity',
      actions: [],
      transaction_data: {
        entity_runtime_id: runtimeId,
        action_type: 'attack',
        hotbar_slot: hotbarSlot,
        held_item: heldItem,
        player_pos: { ...position },
        click_pos: clickPosition ? { ...clickPosition } : { x: 0, y: 1, z: 0 }
      }
    }
  }
}

export function createEntityMouseOver (runtimeId) {
  return { action_id: 'mouse_over_entity', target_entity_id: runtimeId, has_position: false }
}
