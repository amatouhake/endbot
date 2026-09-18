// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

export const EMPTY_ITEM = { network_id: 0, count: 0, metadata: 0, has_stack_id: false, block_runtime_id: 0 }

export function hasUsableHeldItem (item) {
  return item != null && Number(item.network_id) !== 0
}

export function serverRotation (packet) {
  if (!Number.isFinite(packet?.yaw) || !Number.isFinite(packet?.pitch)) return undefined
  return { yaw: packet.yaw, pitch: packet.pitch }
}

export function addJumpInputFlags (inputData, { started, airborne }) {
  if (started) inputData.push('jump_down', 'start_jumping')
  if (airborne) inputData.push('jumping')
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
