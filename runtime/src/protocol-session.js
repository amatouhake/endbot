// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import path from 'node:path'

import { createLocalOwnerbotAuth } from './local-identity.js'

function vector (value) { return { x: value.x, y: value.y, z: value.z } }
const EMPTY_ITEM = { network_id: 0 }

export class BedrockSession extends EventEmitter {
  constructor (profile, options) {
    super()
    this.profile = profile
    this.options = options
    this.position = undefined
    this.entities = new Map()
    this.tick = 1n
    this.inputs = undefined
    this.disconnecting = false
    this.heldItem = EMPTY_ITEM
    this.hotbarSlot = 0
  }

  async connect () {
    const protocolModule = await import('bedrock-protocol')
    const protocol = protocolModule.default ?? protocolModule
    const auth = createLocalOwnerbotAuth({
      username: this.profile.name,
      identityId: this.profile.identityId,
      privateKeyPath: this.options.ownerPrivateKeyPath,
      publicKeyPath: this.options.ownerPublicKeyPath,
      issuer: this.options.localAuthIssuer,
      audience: this.options.localAuthAudience
    })
    let pin
    try { pin = fs.readFileSync(this.options.serverIdentityPinPath, 'utf8').trim() } catch (error) {
      if (error.code !== 'ENOENT') throw error
    }
    this.client = protocol.createClient({
      host: this.options.serverHost,
      port: this.options.serverPort,
      username: this.profile.name,
      version: this.options.gameVersion,
      offline: false,
      authflow: auth.authflow,
      skinData: { SelfSignedId: this.profile.identityId },
      raknetBackend: 'nethernet',
      nethernetServerKeyPin: pin || undefined,
      onNetherNetServerTrust: identity => {
        if (!['127.0.0.1', 'localhost', '::1'].includes(this.options.serverHost)) return false
        fs.mkdirSync(path.dirname(this.options.serverIdentityPinPath), { recursive: true, mode: 0o700 })
        fs.writeFileSync(this.options.serverIdentityPinPath, `${identity.pin}\n`, { mode: 0o600, flag: 'wx' })
        return true
      },
      connectTimeout: this.options.connectTimeoutMs
    })
    this.#wireClient()
    return await new Promise((resolve, reject) => {
      let settled = false
      const fail = error => {
        if (!settled) {
          settled = true
          reject(error)
        }
      }
      this.client.once('error', fail)
      this.client.once('kick', packet => fail(new Error(`Kicked: ${packet.message}`)))
      this.client.once('spawn', () => {
        if (settled) return
        settled = true
        this.client.queue('serverbound_loading_screen', { type: 2 })
        this.timer = setInterval(() => this.#sendTick(), 50)
        resolve()
      })
    })
  }

  applyInputs (inputs) { this.inputs = inputs }

  disconnect (reason = 'Endbot disconnect') {
    this.disconnecting = true
    clearInterval(this.timer)
    if (this.client) this.client.close(reason)
  }

  #wireClient () {
    this.client.on('start_game', packet => {
      this.position = vector(packet.player_position)
      this.groundY = this.position.y
      this.client.queue('serverbound_loading_screen', { type: 1 })
    })
    this.client.on('correct_player_move_prediction', packet => {
      if (packet.prediction_type !== 'player') return
      this.position = vector(packet.position)
      const next = BigInt(packet.tick) + 1n
      if (next > this.tick) this.tick = next
    })
    this.client.on('move_player', packet => {
      if (String(packet.runtime_id) === String(this.client.entityId)) {
        this.position = vector(packet.position)
        const next = BigInt(packet.tick) + 1n
        if (next > this.tick) this.tick = next
      } else {
        const entity = this.entities.get(String(packet.runtime_id))
        if (entity) entity.position = vector(packet.position)
      }
    })
    for (const event of ['add_player', 'add_entity']) {
      this.client.on(event, packet => this.entities.set(String(packet.runtime_id), {
        runtimeId: packet.runtime_id,
        position: vector(packet.position),
        type: packet.entity_type ?? 'minecraft:player'
      }))
    }
    this.client.on('move_entity', packet => {
      const entity = this.entities.get(String(packet.runtime_entity_id))
      if (entity) entity.position = vector(packet.position)
    })
    this.client.on('remove_entity', packet => this.entities.delete(String(packet.entity_id_self)))
    this.client.on('mob_equipment', packet => {
      if (String(packet.runtime_entity_id) !== String(this.client.entityId)) return
      this.heldItem = packet.item
      this.hotbarSlot = packet.selected_slot
    })
    this.client.on('error', error => {
      if (!this.disconnecting) this.emit('close', error)
    })
    this.client.on('kick', packet => {
      if (!this.disconnecting) this.emit('close', new Error(`Kicked: ${packet.message}`))
    })
    this.client.on('close', reason => {
      clearInterval(this.timer)
      if (!this.disconnecting) this.emit('close', new Error(`Connection closed: ${reason || 'unknown reason'}`))
    })
  }

  #sendTick () {
    if (!this.position || !this.inputs || this.client.status !== 4) return
    const state = this.inputs.step()
    const previous = vector(this.position)
    const move = { x: 0, z: 0 }
    if (state.movement === 'forward') move.z = 1
    if (state.movement === 'backward') move.z = -1
    if (state.movement === 'left') move.x = -1
    if (state.movement === 'right') move.x = 1
    const radians = state.yaw * Math.PI / 180
    let speed = state.sprint ? 0.28 : state.sneak ? 0.065 : 0.215
    if (!state.movement) speed = 0
    this.position.x += (-Math.sin(radians) * move.z + Math.cos(radians) * move.x) * speed
    this.position.z += (Math.cos(radians) * move.z + Math.sin(radians) * move.x) * speed
    const inputData = []
    if (move.z > 0) inputData.push('up')
    if (move.z < 0) inputData.push('down')
    if (move.x < 0) inputData.push('left')
    if (move.x > 0) inputData.push('right')
    if (state.sprint) inputData.push('sprinting')
    if (state.sneak) inputData.push('sneaking')
    if (state.triggered.includes('jump')) {
      inputData.push('jump_down', 'start_jumping', 'jumping')
      this.verticalVelocity = this.verticalVelocity || 0.42
    }
    if (this.verticalVelocity) {
      this.position.y += this.verticalVelocity
      this.verticalVelocity = (this.verticalVelocity - 0.08) * 0.98
      if (this.position.y <= this.groundY && this.verticalVelocity < 0) {
        this.position.y = this.groundY
        this.verticalVelocity = 0
      } else inputData.push('jumping')
    }
    if (state.triggered.includes('attack')) this.#attack(inputData, state)
    let transaction
    if (state.triggered.includes('use')) {
      inputData.push('item_interact', 'start_using_item')
      transaction = {
        legacy: { legacy_request_id: 0 },
        actions: [],
        data: {
          action_type: 'click_air', trigger_type: 'player_input', block_position: { x: 0, y: 0, z: 0 },
          face: -1, hotbar_slot: this.hotbarSlot, held_item: this.heldItem, player_pos: vector(this.position),
          click_pos: { x: 0, y: 0, z: 0 }, block_runtime_id: 0, client_prediction: 'failure'
        }
      }
    }
    this.client.queue('player_auth_input', {
      pitch: state.pitch,
      yaw: state.yaw,
      position: vector(this.position),
      move_vector: move,
      head_yaw: state.yaw,
      input_data: inputData,
      input_mode: 'mouse',
      play_mode: 'normal',
      interaction_model: 'crosshair',
      interact_rotation: { x: state.pitch, z: state.yaw },
      tick: this.tick,
      delta: { x: this.position.x - previous.x, y: this.position.y - previous.y, z: this.position.z - previous.z },
      transaction,
      item_stack_request: undefined,
      block_action: undefined,
      vehicle_rotation: undefined,
      predicted_vehicle: undefined,
      analogue_move_vector: move,
      camera_orientation: this.#lookVector(state),
      raw_move_vector: move
    })
    this.tick += 1n
  }

  #lookVector ({ yaw, pitch }) {
    const y = -Math.sin(pitch * Math.PI / 180)
    const horizontal = Math.cos(pitch * Math.PI / 180)
    return { x: -Math.sin(yaw * Math.PI / 180) * horizontal, y, z: Math.cos(yaw * Math.PI / 180) * horizontal }
  }

  #attack (inputData, state) {
    const target = this.#target(state)
    if (!target) {
      inputData.push('missed_swing')
      this.client.queue('animate', { action_id: 'swing_arm', runtime_entity_id: this.client.entityId, data: 0, has_swing_source: false })
      return
    }
    this.client.queue('inventory_transaction', {
      transaction: {
        legacy: { legacy_request_id: 0 },
        transaction_type: 'item_use_on_entity',
        actions: [],
        transaction_data: {
          entity_runtime_id: target.runtimeId,
          action_type: 'attack',
          hotbar_slot: this.hotbarSlot,
          held_item: this.heldItem,
          player_pos: vector(this.position),
          click_pos: { x: 0, y: 1, z: 0 }
        }
      }
    })
  }

  #target (state) {
    const look = this.#lookVector(state)
    let best
    for (const entity of this.entities.values()) {
      const delta = { x: entity.position.x - this.position.x, y: entity.position.y + 1 - (this.position.y + 1.62), z: entity.position.z - this.position.z }
      const distance = Math.hypot(delta.x, delta.y, delta.z)
      if (distance > 4.5 || distance < 0.01) continue
      const alignment = (delta.x * look.x + delta.y * look.y + delta.z * look.z) / distance
      if (alignment < 0.94) continue
      if (!best || alignment > best.alignment) best = { ...entity, alignment }
    }
    return best
  }
}

export function createSessionFactory (options) {
  return profile => new BedrockSession(profile, options)
}
