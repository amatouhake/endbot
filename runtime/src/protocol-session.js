// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import { EventEmitter } from 'node:events'
import fs from 'node:fs'

import { createLocalOwnerbotAuth, loadOrCreatePersistentArtifact, loadPersistentArtifact } from './local-identity.js'
import {
  addJumpInputFlags,
  addToggleInputFlags,
  createAttackTransaction,
  createBlockInteractionAction,
  createBlockInteractionInput,
  createDropTransaction,
  createEntityMouseOver,
  createReleaseTransaction,
  createUseTransaction,
  EMPTY_ITEM,
  hasUsableHeldItem,
  serverRotation
} from './packets.js'

function vector (value) { return { x: value.x, y: value.y, z: value.z } }

function loadServerIdentityPin (filename) {
  const value = fs.readFileSync(filename, 'utf8').trim()
  if (!/^sha256:[0-9a-f]{64}$/.test(value)) {
    throw new Error('BDS NetherNet server identity pin is invalid')
  }
  return value
}

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
    this.inventory = []
    this.closeEmitted = false
    this.transportClosed = false
    this.spawned = false
    this.respawnPending = false
    this.respawnReadySent = false
    this.disconnectPromise = undefined
    this.connectPromise = undefined
    this.terminalError = undefined
    this.failureCloseRequested = false
    this.authoritativeLook = undefined
    this.activeItemUse = undefined
    this.releaseItemUseNextTick = false
    this.onGround = true
    this.jumping = false
    this.pendingHandledTeleport = false
    this.pendingInteraction = undefined
    this.interactionStopNextTick = undefined
  }

  connect () {
    if (!this.connectPromise) this.connectPromise = this.#connect()
    return this.connectPromise
  }

  async #connect () {
    if (this.disconnecting) throw new Error('Bedrock session connection was canceled')
    const protocolModule = await (this.options.protocolLoader?.() ?? import('bedrock-protocol'))
    // Dynamic module loading cannot be aborted. Recheck ownership immediately
    // after it settles so a concurrent disconnect cannot create a late client.
    if (this.disconnecting) throw new Error('Bedrock session connection was canceled')
    const protocol = protocolModule.default ?? protocolModule
    const auth = createLocalOwnerbotAuth({
      username: this.profile.name,
      identityId: this.profile.identityId,
      privateKeyPath: this.options.ownerPrivateKeyPath,
      publicKeyPath: this.options.ownerPublicKeyPath,
      issuer: this.options.localAuthIssuer,
      audience: this.options.localAuthAudience
    })
    const pin = loadPersistentArtifact(
      this.options.serverIdentityPinPath,
      loadServerIdentityPin,
      'BDS NetherNet server identity pin'
    ).value
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
        const winner = loadOrCreatePersistentArtifact(
          this.options.serverIdentityPinPath,
          () => `${identity.pin}\n`,
          loadServerIdentityPin,
          'BDS NetherNet server identity pin'
        ).value
        return winner === identity.pin
      },
      connectTimeout: this.options.connectTimeoutMs
    })
    this.#wireClient()
    return await new Promise((resolve, reject) => {
      let settled = false
      const closed = reason => fail(this.terminalError ?? new Error(`Connection closed: ${reason || 'unknown reason'}`))
      const fail = error => {
        if (!settled) {
          settled = true
          reject(error)
        }
      }
      this.client.once('close', closed)
      this.client.once('spawn', () => {
        if (settled) return
        settled = true
        this.client.off('close', closed)
        this.spawned = true
        this.client.queue('serverbound_loading_screen', { type: 2 })
        this.timer = setInterval(() => {
          try {
            this.#sendTick()
          } catch (error) {
            this.#fail(error)
          }
        }, 50)
        resolve()
      })
    })
  }

  applyInputs (inputs) {
    const firstAttachment = this.inputs !== inputs
    this.inputs = inputs
    if (firstAttachment && this.authoritativeLook) {
      inputs.setLook(this.authoritativeLook.yaw, this.authoritativeLook.pitch)
    }
    const useMode = inputs.actionMode('use')
    if (this.activeItemUse && useMode !== this.activeItemUse.mode) this.#releaseItemUse()
  }

  selectedHotbarSlot () { return this.hotbarSlot }

  selectHotbar (slot) {
    if (!Number.isSafeInteger(slot) || slot < 0 || slot > 8) throw new Error('Hotbar slot must be from 1 to 9')
    if (!this.client || this.transportClosed || this.client.status !== 4) throw new Error('Bot is not online')
    this.hotbarSlot = slot
    this.#selectHeldItem()
    this.client.queue('mob_equipment', {
      runtime_entity_id: this.client.entityId,
      item: this.heldItem,
      slot,
      selected_slot: slot,
      window_id: 'inventory'
    })
  }

  interactBlock ({ blockPosition, blockRuntimeId, face }) {
    if (!this.position) throw new Error('Bot position is not initialized')
    if (!this.client || this.transportClosed || this.client.status !== 4) throw new Error('Bot is not online')
    if (!Array.isArray(blockPosition) || blockPosition.length !== 3 || !blockPosition.every(Number.isSafeInteger)) {
      throw new Error('Block interaction requires integer block coordinates')
    }
    if (!Number.isSafeInteger(face) || face < 0 || face > 5) throw new Error('Invalid block face')
    if (!Number.isSafeInteger(blockRuntimeId) || blockRuntimeId < 0) throw new Error('Invalid block runtime ID')
    if (this.pendingInteraction) throw new Error('A block interaction is already pending')
    const action = operation => createBlockInteractionAction({
      runtimeEntityId: this.client.entityId,
      blockPosition,
      face,
      action: operation
    })
    this.client.queue('player_action', action('start_item_use_on'))
    this.pendingInteraction = {
      transaction: createBlockInteractionInput({
        hotbarSlot: this.hotbarSlot,
        heldItem: this.heldItem,
        position: this.position,
        blockPosition,
        blockRuntimeId,
        face
      }),
      stopAction: action('stop_item_use_on')
    }
  }

  dropSelected (stack) {
    if (!this.client || this.transportClosed || this.client.status !== 4) throw new Error('Bot is not online')
    this.client.queue('inventory_transaction', createDropTransaction({
      hotbarSlot: this.hotbarSlot,
      heldItem: this.heldItem,
      stack
    }))
  }

  disconnect (reason = 'Endbot disconnect') {
    if (this.disconnectPromise) return this.disconnectPromise
    this.disconnecting = true
    clearInterval(this.timer)
    this.disconnectPromise = this.#disconnect(reason).finally(() => { this.disconnectPromise = undefined })
    return this.disconnectPromise
  }

  async #disconnect (reason) {
    // A disconnect issued while connect() is loading its protocol dependency
    // owns that initialization until it observes cancellation. Do not report a
    // closed session while initialization can still create a transport.
    if (!this.client && this.connectPromise) {
      try { await this.connectPromise } catch {}
    }
    if (!this.client || this.transportClosed) return
    await new Promise((resolve, reject) => {
      let settled = false
      const finish = error => {
        if (settled) return
        settled = true
        clearTimeout(timeout)
        this.client.off('close', closed)
        if (error) reject(error)
        else resolve()
      }
      const closed = () => finish()
      const timeout = setTimeout(() => {
        this.client.close('Endbot disconnect timeout')
        finish(new Error('Timed out waiting for the Bedrock session to close'))
      }, 5000)
      timeout.unref?.()
      this.client.once('close', closed)
      try {
        this.client.disconnect(reason)
      } catch (error) {
        finish(error)
      }
    })
  }

  #wireClient () {
    this.client.on('packet_violation_warning', packet => {
      this.#fail(new Error(`BDS rejected a malformed protocol packet: ${packet.reason}`))
    })
    this.client.on('start_game', packet => {
      this.position = vector(packet.player_position)
      this.onGround = true
      this.verticalVelocity = 0
      this.#setAuthoritativeLook(packet)
      this.client.queue('serverbound_loading_screen', { type: 1 })
    })
    this.client.on('correct_player_move_prediction', packet => {
      if (packet.prediction_type !== 'player') return
      this.position = vector(packet.position)
      this.onGround = Boolean(packet.on_ground)
      this.verticalVelocity = this.onGround ? 0 : Number(packet.delta?.y ?? this.verticalVelocity ?? 0)
      if (this.onGround) this.jumping = false
      const next = BigInt(packet.tick) + 1n
      if (next > this.tick) this.tick = next
    })
    this.client.on('move_player', packet => {
      if (String(packet.runtime_id) === String(this.client.entityId)) {
        this.position = vector(packet.position)
        this.onGround = Boolean(packet.on_ground)
        if (packet.mode === 'teleport') {
          this.pendingHandledTeleport = true
          this.verticalVelocity = this.onGround ? 0 : -0.08
          this.jumping = false
        } else if (this.onGround) {
          this.verticalVelocity = 0
          this.jumping = false
        }
        this.#setAuthoritativeLook(packet)
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
        uniqueId: packet.unique_id,
        position: vector(packet.position),
        type: packet.entity_type ?? 'minecraft:player'
      }))
    }
    this.client.on('move_entity', packet => {
      const entity = this.entities.get(String(packet.runtime_entity_id))
      if (entity) entity.position = vector(packet.position)
    })
    this.client.on('move_entity_delta', packet => {
      const entity = this.entities.get(String(packet.runtime_entity_id))
      if (!entity) return
      // Protocol 2193 names this packet historically, but its present optional
      // coordinates are absolute. Preserve omitted axes from the last update.
      for (const axis of ['x', 'y', 'z']) {
        if (Number.isFinite(packet[axis])) entity.position[axis] = packet[axis]
      }
    })
    this.client.on('remove_entity', packet => {
      const uniqueId = String(packet.entity_id_self)
      for (const [runtimeId, entity] of this.entities) {
        if (String(entity.uniqueId) === uniqueId) this.entities.delete(runtimeId)
      }
    })
    this.client.on('inventory_content', packet => {
      if (!this.#isPlayerInventory(packet.window_id)) return
      this.inventory = packet.input
      this.#selectHeldItem()
    })
    this.client.on('inventory_slot', packet => {
      if (!this.#isPlayerInventory(packet.window_id)) return
      this.inventory[packet.slot] = packet.item
      if (packet.slot === this.hotbarSlot) this.#selectHeldItem()
    })
    this.client.on('player_hotbar', packet => {
      if (!this.#isPlayerInventory(packet.window_id)) return
      this.hotbarSlot = packet.selected_slot
      this.#selectHeldItem()
    })
    this.client.on('mob_equipment', packet => {
      if (String(packet.runtime_entity_id) !== String(this.client.entityId)) return
      this.heldItem = packet.item
      this.hotbarSlot = packet.selected_slot
    })
    this.client.on('set_health', packet => {
      if (Number(packet.health) <= 0) this.#requestRespawn()
      else {
        this.respawnPending = false
        this.respawnReadySent = false
      }
    })
    this.client.on('death_info', () => this.#requestRespawn())
    this.client.on('respawn', packet => this.#handleRespawn(packet))
    this.client.on('error', error => {
      this.#fail(error)
    })
    this.client.on('kick', packet => {
      this.#fail(new Error(`Kicked: ${packet.message}`))
    })
    this.client.on('close', reason => {
      clearInterval(this.timer)
      this.transportClosed = true
      this.#emitClose(this.terminalError ?? new Error(`Connection closed: ${reason || 'unknown reason'}`))
    })
  }

  #emitClose (error) {
    if (this.disconnecting || this.closeEmitted) return
    this.closeEmitted = true
    this.emit('close', error)
  }

  #fail (error) {
    if (this.transportClosed) return
    this.terminalError ??= error
    clearInterval(this.timer)
    if (this.failureCloseRequested) return
    this.failureCloseRequested = true
    this.client?.close('Endbot protocol error')
  }

  #requestRespawn () {
    if (this.respawnPending || !this.client || this.client.status !== 4) return
    this.respawnPending = true
    this.respawnReadySent = false
    this.inputs?.stopAll()
    this.#sendRespawnAction()
  }

  #sendRespawnAction () {
    this.client.queue('player_action', {
      runtime_entity_id: this.client.entityId,
      action: 'respawn',
      position: { x: 0, y: 0, z: 0 },
      result_position: { x: 0, y: 0, z: 0 },
      face: -1
    })
  }

  #handleRespawn (packet) {
    if (packet.state === 0 && !this.respawnReadySent) {
      this.respawnReadySent = true
      this.client.queue('respawn', {
        position: { x: 0, y: 0, z: 0 },
        state: 2,
        runtime_entity_id: this.client.entityId
      })
      return
    }
    if (packet.state !== 1) return
    this.position = vector(packet.position)
    this.verticalVelocity = 0
    this.onGround = true
    this.jumping = false
    if (this.respawnPending && this.spawned) this.#sendRespawnAction()
    this.respawnPending = false
  }

  #sendTick () {
    if (!this.position || !this.inputs || this.client.status !== 4) return
    if (this.interactionStopNextTick) {
      this.client.queue('player_action', this.interactionStopNextTick)
      this.interactionStopNextTick = undefined
    }
    if (this.activeItemUse && this.releaseItemUseNextTick) this.#releaseItemUse()
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
    addToggleInputFlags(inputData, state)
    const startedJump = state.triggered.includes('jump')
    if (startedJump && this.onGround) {
      this.verticalVelocity = 0.42
      this.onGround = false
      this.jumping = true
    }
    if (!this.onGround) {
      this.position.y += this.verticalVelocity
      this.verticalVelocity = (this.verticalVelocity - 0.08) * 0.98
    }
    addJumpInputFlags(inputData, { started: startedJump, airborne: this.jumping })
    if (this.pendingHandledTeleport) {
      inputData.push('handled_teleport')
      this.pendingHandledTeleport = false
    }
    if (this.onGround) inputData.push('vertical_collision')
    let transaction
    if (this.pendingInteraction) {
      const interaction = this.pendingInteraction
      this.pendingInteraction = undefined
      inputData.push('perform_item_interaction')
      transaction = interaction.transaction
      this.interactionStopNextTick = interaction.stopAction
    }
    if (state.triggered.includes('attack')) this.#attack(inputData, state)
    if (state.triggered.includes('use') && !this.activeItemUse && hasUsableHeldItem(this.heldItem)) {
      const mode = this.inputs.actionMode('use') ?? 'once'
      inputData.push('start_using_item')
      this.activeItemUse = { hotbarSlot: this.hotbarSlot, heldItem: this.heldItem, mode }
      this.releaseItemUseNextTick = mode !== 'continuous'
      this.client.queue('inventory_transaction', createUseTransaction({
        hotbarSlot: this.hotbarSlot,
        heldItem: this.heldItem,
        position: this.position
      }))
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

  #setAuthoritativeLook (packet) {
    const rotation = serverRotation(packet)
    if (!rotation) return
    this.authoritativeLook = rotation
    this.inputs?.setLook(rotation.yaw, rotation.pitch)
  }

  #releaseItemUse () {
    if (!this.activeItemUse) return
    const use = this.activeItemUse
    this.activeItemUse = undefined
    this.releaseItemUseNextTick = false
    if (!this.client || this.transportClosed || this.client.status !== 4 || !this.position) return
    this.client.queue('inventory_transaction', createReleaseTransaction({
      hotbarSlot: use.hotbarSlot,
      heldItem: use.heldItem,
      position: this.position
    }))
  }

  #attack (inputData, state) {
    const target = this.#target(state)
    if (!target) {
      inputData.push('missed_swing')
      this.client.queue('animate', { action_id: 'swing_arm', runtime_entity_id: this.client.entityId, data: 0, has_swing_source: false })
      return
    }
    this.client.queue('interact', createEntityMouseOver(target.runtimeId))
    this.client.queue('inventory_transaction', createAttackTransaction({
      runtimeId: target.runtimeId,
      hotbarSlot: this.hotbarSlot,
      heldItem: this.heldItem,
      position: this.position
    }))
    this.client.queue('animate', {
      action_id: 'swing_arm',
      runtime_entity_id: this.client.entityId,
      data: 0,
      has_swing_source: false
    })
  }

  #isPlayerInventory (windowId) {
    return windowId === 0 || windowId === 'inventory'
  }

  #selectHeldItem () {
    this.heldItem = this.inventory[this.hotbarSlot] ?? EMPTY_ITEM
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
