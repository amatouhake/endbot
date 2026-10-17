// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import { EventEmitter } from 'node:events'
import { randomUUID } from 'node:crypto'

import { botChunkRadius, LOOPBACK_HOSTS, serverTransport } from './config.js'
import { discoverServer } from './discovery.js'
import { decodeHotbar, INVENTORY_EVENT, sameItem } from './inventory-sync.js'
import { createLocalBotAuth } from './local-identity.js'
import {
  addJumpInputFlags,
  addToggleInputFlags,
  createAttackTransaction,
  createBlockInteractionAction,
  createBlockInteractionInput,
  createDropTransaction,
  createEntityMouseOver,
  createMiningAction,
  createReleaseTransaction,
  createUseInput,
  EMPTY_ITEM,
  hasUsableHeldItem,
  serverRotation
} from './packets.js'

function vector (value) { return { x: value.x, y: value.y, z: value.z } }

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
    this.announcedEquipment = undefined
    this.hotbarSlot = 0
    this.inventory = []
    this.itemPalette = new Map()
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
    this.pendingHandledTeleport = false
    this.pendingInteraction = undefined
    this.interactionStopNextTick = undefined
    this.horizontalVelocity = { x: 0, z: 0 }
    this.mining = undefined
    this.pendingBlockActions = []
  }

  connect () {
    if (!this.connectPromise) this.connectPromise = this.#connect()
    return this.connectPromise
  }

  async #connect () {
    if (this.disconnecting) throw new Error('Bedrock session connection was canceled')
    // BDS regenerates its NetherNet DTLS identity on every start and upstream
    // bedrock-protocol has no identity hook, so trust rests on the loopback
    // path plus the owner-signed, cpk-bound, single-use Bot token
    // (docs/SECURITY.md). Recheck here for sessions built without loadConfig.
    if (!LOOPBACK_HOSTS.has(this.options.serverHost)) {
      throw new Error('Endbot connects to loopback BDS servers only')
    }
    const transport = serverTransport(this.options.transport)
    this.chunkRadius = botChunkRadius(this.profile.chunkRadius ?? this.options.chunkRadius)
    const protocolModule = await (this.options.protocolLoader?.() ?? import('bedrock-protocol'))
    // Dynamic module loading cannot be aborted. Recheck ownership immediately
    // after it settles so a concurrent disconnect cannot create a late client.
    if (this.disconnecting) throw new Error('Bedrock session connection was canceled')
    const protocol = protocolModule.default ?? protocolModule
    // With expected server identity configured, connect only to the BDS that
    // advertises it; otherwise keep upstream first-reply discovery.
    let target
    if (transport === 'nethernet' && this.options.serverName !== undefined && this.options.levelName !== undefined) {
      target = await (this.options.discover ?? discoverServer)({
        host: this.options.serverHost,
        serverName: this.options.serverName,
        levelName: this.options.levelName,
        timeoutMs: this.options.discoveryTimeoutMs
      })
      if (this.disconnecting) throw new Error('Bedrock session connection was canceled')
    }
    const auth = createLocalBotAuth({
      username: this.profile.name,
      identityId: this.profile.identityId,
      privateKeyPath: this.options.ownerPrivateKeyPath,
      publicKeyPath: this.options.ownerPublicKeyPath,
      issuer: this.options.localAuthIssuer,
      audience: this.options.localAuthAudience
    })
    this.client = protocol.createClient({
      host: this.options.serverHost,
      port: this.options.serverPort,
      username: this.profile.name,
      version: this.options.gameVersion,
      offline: false,
      authflow: auth.authflow,
      skinData: { SelfSignedId: this.profile.identityId },
      transport,
      ...(transport === 'nethernet'
        ? { nethernet: target ? { signalling: 'lan', networkId: target.networkId } : { signalling: 'lan' } }
        : { raknetBackend: 'raknet-native', followPort: false }),
      // RakNet must dial the configured loopback port directly. Do not discover
      // another server or follow an advertised port; the locked version is explicit.
      skipPing: transport === 'raknet' || Boolean(target),
      connectTimeout: this.options.connectTimeoutMs
    })
    // Upstream's initial chunk request reads this property, not an option.
    this.client.viewDistance = this.chunkRadius
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
        this.#announceHeldItem()
        this.#requestChunks()
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
    if (this.activeItemUse) {
      const changed = inputs.actionRevision('use') !== this.activeItemUse.revision
      if (inputs.actionMode('use') !== this.activeItemUse.mode ||
          (changed && (this.activeItemUse.timed || this.activeItemUse.holdTicks !== undefined ||
            inputs.hasTimedUse()))) this.#releaseItemUse()
      else this.activeItemUse.revision = inputs.actionRevision('use')
    }
  }

  selectedHotbarSlot () { return this.hotbarSlot }

  miningStatus () {
    const job = this.mining ? { ...this.mining, blockPosition: [...this.mining.blockPosition] } : null
    if (!this.miningLoop) return job
    return { ...job, state: job?.state === 'active' ? 'active' : 'waiting',
      reason: job?.state === 'active' ? job.reason : 'waiting for a reachable block',
      continuous: true, loopId: this.miningLoop.id, blocksBroken: this.miningLoop.blocksBroken,
      ...(this.miningLoop.remainingTicks === undefined ? {} : { remainingTicks: this.miningLoop.remainingTicks }),
      ...(this.miningLoop.blockCount === undefined ? {} : { blockCount: this.miningLoop.blockCount }) }
  }

  startMiningLoop (durationTicks = undefined, blockCount = undefined) {
    if (durationTicks !== undefined && (!Number.isSafeInteger(durationTicks) || durationTicks < 1 || durationTicks > 72_000)) {
      throw new Error('Destroy duration must be an integer from 1 to 72000 ticks')
    }
    if (blockCount !== undefined && (!Number.isSafeInteger(blockCount) || blockCount < 1 || blockCount > 72_000)) {
      throw new Error('Destroy block count must be an integer from 1 to 72000 blocks')
    }
    this.#assertMiningAvailable()
    this.stopMining('continuous mining restarted')
    this.mining = undefined
    // `blocksBroken` counts completed targets for `status`. A `count` budget
    // ends the loop as soon as that many blocks are gone: waiting for the next
    // target costs nothing, unlike the wall-clock `for` tick budget.
    this.miningLoop = { id: randomUUID(), leaseTicks: 0, blocksBroken: 0,
      ...(durationTicks === undefined ? {} : { remainingTicks: durationTicks }),
      ...(blockCount === undefined ? {} : { blockCount }) }
  }

  continueMining ({ loopId, ...target }) {
    if (!this.miningLoop || this.miningLoop.id !== loopId) return false
    if (target.blockPosition !== undefined) this.startMining({ ...target, loopId })
    this.miningLoop.leaseTicks = 0
    return true
  }

  #assertMiningAvailable () {
    if (!this.client || this.transportClosed || this.client.status !== 4 || !this.position || !this.inputs) {
      throw new Error('Bot is not online')
    }
    if (!this.serverAuthoritativeMining) throw new Error('Destroy requires server-authoritative block breaking')
  }

  startMining ({ blockPosition, blockRuntimeId, blockName, face = 1, dimension, loopId }) {
    if (loopId !== undefined && this.miningLoop?.id !== loopId) return
    this.#assertMiningAvailable()
    if (!Array.isArray(blockPosition) || blockPosition.length !== 3 ||
        !blockPosition.every(value => Number.isSafeInteger(value) && Math.abs(value) <= 30_000_000)) {
      throw new Error('Destroy requires integer block coordinates within world bounds')
    }
    if (!Number.isSafeInteger(face) || face < 0 || face > 5) throw new Error('Invalid block face')
    if (!Number.isSafeInteger(blockRuntimeId) || blockRuntimeId < 0) throw new Error('Invalid block runtime ID')
    const dimensions = { Overworld: 0, overworld: 0, 'minecraft:overworld': 0,
      Nether: 1, nether: 1, 'minecraft:nether': 1, TheEnd: 2, end: 2, 'minecraft:the_end': 2, 'minecraft:end': 2 }
    if (dimension !== undefined && (dimensions[dimension] ?? dimension) !== (dimensions[this.dimension] ?? this.dimension)) {
      throw new Error('The Bot changed dimension; retry the command')
    }
    if (!this.#miningInReach(blockPosition)) throw new Error('Target block is out of reach')
    if (loopId === undefined) this.miningLoop = undefined
    if (this.mining?.state === 'active' && this.mining.blockRuntimeId === blockRuntimeId &&
        this.mining.blockPosition.every((value, index) => value === blockPosition[index])) return
    this.stopMining('target changed', 'canceled', false, loopId !== undefined)
    this.#releaseItemUse()
    this.pendingInteraction = undefined
    this.mining = { state: 'active', reason: 'waiting for BDS', blockPosition: [...blockPosition], blockRuntimeId,
      blockName: typeof blockName === 'string' ? blockName : 'unknown', face, ticks: 0 }
    this.pendingBlockActions.push(createMiningAction('start_break', blockPosition, face))
  }

  stopMining (reason = 'stopped', state = 'canceled', discard = false, preserveLoop = false) {
    const looping = this.miningLoop !== undefined
    // A loop that ends keeps its completed-block count (and its count limit)
    // on the finished job so `status` can report what it broke.
    const carried = looping && !preserveLoop ? {
      blocksBroken: this.miningLoop.blocksBroken,
      ...(this.miningLoop.blockCount === undefined ? {} : { blockCount: this.miningLoop.blockCount })
    } : undefined
    if (!preserveLoop) this.miningLoop = undefined
    if (this.mining?.state === 'active') {
      const job = this.mining
      if (discard) this.pendingBlockActions = []
      else if (job.ticks > 0) this.pendingBlockActions.push(createMiningAction('abort_break', job.blockPosition, job.face))
      else this.pendingBlockActions = this.pendingBlockActions.filter(action => action.action !== 'start_break')
      job.state = state
      job.reason = reason
      if (carried !== undefined) Object.assign(job, carried)
    }
    if (looping && !preserveLoop && this.mining?.state !== 'active') {
      this.mining = { ...this.mining, blockPosition: this.mining?.blockPosition ?? [], state, reason,
        ...(carried ?? {}) }
    }
    if (discard) this.pendingBlockActions = []
  }

  #restartMining () {
    const job = this.mining
    if (job?.state !== 'active') return
    if (job.ticks > 0) this.pendingBlockActions.push(createMiningAction('abort_break', job.blockPosition, job.face))
    else this.pendingBlockActions = this.pendingBlockActions.filter(action => action.action !== 'start_break')
    this.pendingBlockActions.push(createMiningAction('start_break', job.blockPosition, job.face))
    job.ticks = 0
  }

  // The watched target changed, so BDS completed (or another actor changed)
  // the block. Continuous loops count the completion for `status`; a `count`
  // budget ends the loop as soon as it is reached instead of retargeting, so
  // the stop lands on an already-finished block and aborts nothing.
  #completeMining () {
    this.stopMining('BDS changed target block', 'completed', false, true)
    const loop = this.miningLoop
    if (loop === undefined) return
    loop.blocksBroken += 1
    if (loop.blockCount !== undefined && loop.blocksBroken >= loop.blockCount) {
      this.stopMining('block count reached', 'completed')
    }
  }

  #miningInReach (blockPosition) {
    return Math.hypot(...blockPosition.map((value, axis) => {
      const coordinate = this.position[['x', 'y', 'z'][axis]]
      return coordinate - Math.max(value, Math.min(coordinate, value + 1))
    })) <= 5.0
  }

  #miningTick (inputData) {
    // The plugin renews this lease while resolving targets. Losing the plugin
    // or its command worker must stop even an already active break attempt.
    if (this.miningLoop && ++this.miningLoop.leaseTicks >= 200) this.stopMining('mining controller timed out', 'failed')
    // `destroy continuous for <ticks>` ends the loop and any block in progress.
    if (this.miningLoop?.remainingTicks !== undefined && this.miningLoop.remainingTicks-- <= 0) {
      this.stopMining('duration elapsed', 'completed')
    }
    if (this.mining?.state === 'active') {
      const job = this.mining
      // A failed block ends single-block mining; a continuous loop retargets.
      if (!this.#miningInReach(job.blockPosition)) this.stopMining('target moved out of reach', 'failed', false, true)
      else if (job.ticks >= 12_000) this.stopMining('timed out waiting for BDS to change target', 'failed', false, true)
      else {
        // With server-authoritative breaking BDS tracks progress itself after
        // start_break. A per-tick continue_break on the same block makes BDS
        // stop and restart the crack, so other players see it flicker.
        job.ticks += 1
        if (job.ticks % 5 === 1) this.client.queue('animate', {
          action_id: 'swing_arm', runtime_entity_id: this.client.entityId, data: 0, has_swing_source: false
        })
      }
    }
    if (!this.pendingBlockActions.length) return undefined
    inputData.push('block_action')
    return this.pendingBlockActions.splice(0)
  }

  setChunkRadius (value) {
    this.chunkRadius = botChunkRadius(value)
    this.serverChunkRadius = undefined
    if (!this.client) return
    this.client.viewDistance = this.chunkRadius
    if (this.spawned && !this.disconnecting && !this.transportClosed && this.client.status === 4) this.#requestChunks()
  }

  selectHotbar (slot) {
    if (!Number.isSafeInteger(slot) || slot < 0 || slot > 8) throw new Error('Hotbar slot must be from 1 to 9')
    if (!this.client || this.transportClosed || this.client.status !== 4) throw new Error('Bot is not online')
    this.#restartMining()
    this.inputs?.cancelTimedUse()
    this.#releaseItemUse()
    this.hotbarSlot = slot
    this.#selectHeldItem()
    this.#announceHeldItem(true)
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
    this.#releaseItemUse()
    this.client.queue('inventory_transaction', createDropTransaction({
      hotbarSlot: this.hotbarSlot,
      heldItem: this.heldItem,
      stack
    }))
    // BDS applies our drop silently, so predict the selected slot exactly
    // like the transaction above (vanilla clients update their own
    // inventory immediately) and re-announce the held item. Without this
    // the cache keeps the dropped item and observers keep rendering it.
    const remaining = Number(this.heldItem.count) - (stack ? Number(this.heldItem.count) : 1)
    this.inventory[this.hotbarSlot] = remaining > 0 ? { ...this.heldItem, count: remaining } : EMPTY_ITEM
    this.selectHotbar(this.hotbarSlot)
  }

  disconnect (reason = 'Endbot disconnect') {
    this.inputs?.cancelTimedUse()
    if (this.disconnectPromise) return this.disconnectPromise
    this.stopMining('disconnected')
    if (this.pendingBlockActions.length && this.client?.status === 4 && !this.transportClosed) this.#sendTick()
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
    this.client.on('item_registry', packet => {
      this.itemPalette = new Map(packet.itemstates.map(item => [item.name, item.runtime_id]))
    })
    this.client.on('script_custom_event', packet => {
      if (packet.event_name !== INVENTORY_EVENT || !this.spawned || this.disconnecting || this.transportClosed) return
      try {
        const slots = decodeHotbar(packet.event_data, this.profile.identityId, this.itemPalette)
        for (let slot = 0; slot < slots.length; slot++) {
          if (sameItem(this.inventory[slot], slots[slot])) continue
          this.inventory[slot] = slots[slot]
        }
        this.#selectHeldItem()
        this.#announceHeldItem()
      } catch {
        // Partial/unknown observations never replace a good native cache.
      }
    })
    this.client.on('chunk_radius_update', packet => {
      if (Number.isSafeInteger(packet.chunk_radius) && packet.chunk_radius > 0) {
        this.serverChunkRadius = packet.chunk_radius
      }
    })
    this.client.on('packet_violation_warning', packet => {
      this.#fail(new Error(`BDS rejected a malformed protocol packet: ${packet.reason}`))
    })
    this.client.on('start_game', packet => {
      this.position = vector(packet.player_position)
      this.dimension = packet.dimension
      this.serverAuthoritativeMining = packet.server_authoritative_block_breaking === true
      this.onGround = true
      this.verticalVelocity = 0
      this.#setAuthoritativeLook(packet)
      this.client.queue('serverbound_loading_screen', { type: 1 })
    })
    this.client.on('change_dimension', packet => {
      this.inputs?.cancelTimedUse()
      this.stopMining('dimension changed', 'canceled', true, true)
      // Discard all old-dimension prediction/targets before acknowledging the
      // transition. Otherwise BDS keeps waiting while we send the old position.
      this.activeItemUse = undefined
      this.releaseItemUseNextTick = false
      this.pendingInteraction = undefined
      this.interactionStopNextTick = undefined
      this.entities.clear()
      this.dimension = packet.dimension
      this.position = vector(packet.position)
      this.horizontalVelocity = { x: 0, z: 0 }
      this.verticalVelocity = -0.08
      this.onGround = false
      this.pendingHandledTeleport = true
      const loading = { loading_screen_id: packet.loading_screen_id }
      this.client.queue('serverbound_loading_screen', { type: 1, ...loading })
      this.#requestChunks()
      this.client.queue('player_action', {
        runtime_entity_id: this.client.entityId,
        action: 'dimension_change_ack',
        position: { x: 0, y: 0, z: 0 },
        result_position: { x: 0, y: 0, z: 0 },
        face: -1
      })
      this.client.queue('serverbound_loading_screen', { type: 2, ...loading })
    })
    this.client.on('set_entity_motion', packet => {
      if (String(packet.runtime_entity_id) !== String(this.client.entityId)) return
      this.horizontalVelocity = { x: packet.velocity.x, z: packet.velocity.z }
      this.verticalVelocity = packet.velocity.y
      if (packet.velocity.y !== 0) this.onGround = false
    })
    this.client.on('correct_player_move_prediction', packet => {
      if (packet.prediction_type !== 'player') return
      this.position = vector(packet.position)
      this.onGround = Boolean(packet.on_ground)
      this.verticalVelocity = this.onGround ? 0 : Number(packet.delta?.y ?? this.verticalVelocity ?? 0)
      // A correction replaces the prediction; its delta includes commanded
      // movement and must not be added again as a fresh knockback impulse.
      this.horizontalVelocity = { x: 0, z: 0 }
      const next = BigInt(packet.tick) + 1n
      if (next > this.tick) this.tick = next
    })
    this.client.on('move_player', packet => {
      if (String(packet.runtime_id) === String(this.client.entityId)) {
        this.position = vector(packet.position)
        this.onGround = Boolean(packet.on_ground)
        if (packet.mode === 'teleport') {
          this.stopMining('teleported', 'canceled', false, true)
          this.horizontalVelocity = { x: 0, z: 0 }
          this.pendingHandledTeleport = true
          this.verticalVelocity = this.onGround ? 0 : -0.08
        } else if (this.onGround) {
          this.verticalVelocity = 0
        }
        this.#setAuthoritativeLook(packet)
        const next = BigInt(packet.tick) + 1n
        if (next > this.tick) this.tick = next
      } else {
        const entity = this.entities.get(String(packet.runtime_id))
        if (entity) entity.position = vector(packet.position)
      }
    })
    this.client.on('update_block', packet => {
      if (packet.layer !== 0 || this.mining?.state !== 'active') return
      const job = this.mining
      if (job.blockPosition.every((value, index) => value === packet.position[['x', 'y', 'z'][index]]) &&
          packet.block_runtime_id !== job.blockRuntimeId) this.#completeMining()
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
      this.#announceHeldItem()
    })
    this.client.on('inventory_slot', packet => {
      if (!this.#isPlayerInventory(packet.window_id)) return
      this.inventory[packet.slot] = packet.item
      if (packet.slot === this.hotbarSlot) {
        this.#selectHeldItem()
        this.#announceHeldItem()
      }
    })
    this.client.on('player_hotbar', packet => {
      if (!this.#isPlayerInventory(packet.window_id)) return
      this.hotbarSlot = packet.selected_slot
      this.#selectHeldItem()
      this.#announceHeldItem()
    })
    this.client.on('mob_equipment', packet => {
      if (String(packet.runtime_entity_id) !== String(this.client.entityId) || !this.#isPlayerInventory(packet.window_id)) return
      this.hotbarSlot = packet.selected_slot
      this.#setHeldItem(packet.item)
      this.#announceHeldItem()
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
      this.stopMining('connection closed', 'canceled', true)
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
    this.stopMining('respawning', 'canceled', true)
    this.activeItemUse = undefined
    this.releaseItemUseNextTick = false
    this.pendingInteraction = undefined
    this.interactionStopNextTick = undefined
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
    this.horizontalVelocity = { x: 0, z: 0 }
    this.onGround = true
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
    // A hold releases after holdTicks use transactions, like a timed use.
    if (this.activeItemUse?.holdTicks !== undefined &&
        this.activeItemUse.heldTicks >= this.activeItemUse.holdTicks) this.#releaseItemUse()
    const state = this.inputs.step()
    if ((this.activeItemUse?.mode === 'continuous' || this.activeItemUse?.holdTicks !== undefined) &&
        this.inputs.actionRevision('use') !== this.activeItemUse.revision) this.#releaseItemUse()
    const previous = vector(this.position)
    const move = { x: 0, z: 0 }
    if (state.movement === 'forward') move.z = 1
    if (state.movement === 'backward') move.z = -1
    if (state.movement === 'left') move.x = 1
    if (state.movement === 'right') move.x = -1
    const radians = state.yaw * Math.PI / 180
    let speed = state.sprint ? 0.28 : state.sneak ? 0.065 : 0.215
    if (!state.movement) speed = 0
    this.position.x += (-Math.sin(radians) * move.z + Math.cos(radians) * move.x) * speed
    this.position.z += (Math.cos(radians) * move.z + Math.sin(radians) * move.x) * speed
    this.position.x += this.horizontalVelocity.x
    this.position.z += this.horizontalVelocity.z
    const drag = this.onGround ? 0.6 : 0.91
    this.horizontalVelocity.x *= drag
    this.horizontalVelocity.z *= drag
    const inputData = []
    if (move.z > 0) inputData.push('up')
    if (move.z < 0) inputData.push('down')
    // Key-state flags follow the commanded direction (vanilla key semantics),
    // not the move-vector sign: the vector carries the facing-relative
    // displacement while the flags report which strafe key is held.
    if (state.movement === 'left') inputData.push('left')
    if (state.movement === 'right') inputData.push('right')
    addToggleInputFlags(inputData, state)
    // Jump is a tap, not a hold: only the trigger tick presses the key and
    // later ticks release it, even while predicted airborne. Holding the
    // `jumping` flag for the whole predicted flight makes BDS bunny-hop on
    // every landing, and a consumed `once` action then leaves `jump stop`
    // with nothing to clear. Continuous mode re-triggers every tick, so it
    // keeps holding by re-pressing instead of by predicted state.
    const startedJump = state.triggered.includes('jump')
    if (startedJump && this.onGround) {
      this.verticalVelocity = 0.42
      this.onGround = false
    }
    if (!this.onGround) {
      this.position.y += this.verticalVelocity
      this.verticalVelocity = (this.verticalVelocity - 0.08) * 0.98
    }
    addJumpInputFlags(inputData, { started: startedJump, airborne: startedJump })
    if (this.pendingHandledTeleport) {
      inputData.push('handled_teleport')
      this.pendingHandledTeleport = false
    }
    if (this.onGround) inputData.push('vertical_collision')
    let transaction
    const blockActions = this.#miningTick(inputData)
    if (this.pendingInteraction) {
      const interaction = this.pendingInteraction
      this.pendingInteraction = undefined
      inputData.push('item_interact')
      transaction = interaction.transaction
      this.interactionStopNextTick = interaction.stopAction
    }
    if (state.triggered.includes('attack')) this.#attack(inputData, state)
    if (state.triggered.includes('use') && !this.activeItemUse && hasUsableHeldItem(this.heldItem)) {
      const mode = this.inputs.actionMode('use') ?? 'once'
      // Block interaction and air use share one packed transaction per tick.
      this.activeItemUse = { hotbarSlot: this.hotbarSlot, heldItem: this.heldItem, mode,
        revision: this.inputs.actionRevision('use'), timed: this.inputs.hasTimedUse(), starting: true,
        holdTicks: this.inputs.holdTicks('use'), heldTicks: 0 }
    }
    if (state.triggered.includes('use') && !hasUsableHeldItem(this.heldItem)) this.inputs.cancelTimedUse()
    if (this.activeItemUse && !transaction) {
      if (this.activeItemUse.starting) {
        inputData.push('start_using_item')
        this.inputs.startTimedUse(this.activeItemUse.revision)
      }
      inputData.push('item_interact')
      transaction = createUseInput({
        hotbarSlot: this.hotbarSlot,
        heldItem: this.heldItem,
        position: this.position,
        triggerType: this.activeItemUse.starting ? 'player_input' : 'simulation_tick'
      })
      this.activeItemUse.starting = false
      this.activeItemUse.heldTicks += 1
      this.releaseItemUseNextTick = this.activeItemUse.mode !== 'continuous' &&
        this.activeItemUse.holdTicks === undefined
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
      block_action: blockActions,
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

  #requestChunks () {
    this.client.queue('request_chunk_radius', { chunk_radius: this.chunkRadius, max_radius: this.chunkRadius })
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
    if (use.holdTicks !== undefined) this.inputs?.finishHold(use.revision)
    if (use.starting) return // A deferred use has not pressed the button yet.
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
    this.#setHeldItem(this.inventory[this.hotbarSlot] ?? EMPTY_ITEM)
  }

  #announceHeldItem (force = false) {
    if (!this.spawned || this.disconnecting || this.transportClosed || this.client?.status !== 4) return
    const previous = this.announcedEquipment
    if (!force && previous?.slot === this.hotbarSlot && sameItem(previous.item, this.heldItem)) return
    // Track what observers were told separately from the inventory cache.
    // Native deltas can arrive before an identical plugin observation.
    this.client.queue('mob_equipment', { runtime_entity_id: this.client.entityId,
      item: this.heldItem, slot: this.hotbarSlot, selected_slot: this.hotbarSlot, window_id: 'inventory' })
    this.announcedEquipment = { slot: this.hotbarSlot, item: structuredClone(this.heldItem) }
  }

  #setHeldItem (item) {
    // Pickups into an empty selected slot and broken tools change the held item;
    // BDS restarts break progress for a new tool, so the runtime does too.
    if (Number(this.heldItem.network_id) !== Number(item.network_id)) this.#restartMining()
    this.heldItem = item
    if (!this.activeItemUse) return
    const use = this.activeItemUse
    if (use.hotbarSlot !== this.hotbarSlot || !hasUsableHeldItem(item) ||
        ['network_id', 'metadata', 'stack_id'].some(key => use.heldItem[key] !== item[key])) {
      // The server consumed/replaced the item. Do not release or keep using
      // the stale stack; continuous mode may start the current item next tick.
      this.activeItemUse = undefined
      this.releaseItemUseNextTick = false
      if (use.holdTicks !== undefined) this.inputs?.finishHold(use.revision)
      if (use.hotbarSlot !== this.hotbarSlot || !hasUsableHeldItem(item) ||
          use.heldItem.network_id !== item.network_id) this.inputs?.cancelTimedUse()
    } else {
      use.heldItem = item
    }
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
