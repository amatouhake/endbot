// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import { EventEmitter } from 'node:events'

import { InputState } from './actions.js'
import { ProfileError, validateBotName } from './profile-store.js'

const ACTIVE_STATES = new Set(['connecting', 'online', 'reconnecting'])

export class BotLifecycle extends EventEmitter {
  constructor ({ store, sessionFactory, reconnect = {}, timers = globalThis }) {
    super()
    this.store = store
    this.sessionFactory = sessionFactory
    this.timers = timers
    this.reconnect = {
      initialDelayMs: reconnect.initialDelayMs ?? 1000,
      maximumDelayMs: reconnect.maximumDelayMs ?? 30_000,
      maximumAttempts: reconnect.maximumAttempts ?? 8,
      sessionReplacementDelayMs: reconnect.sessionReplacementDelayMs ?? 1000
    }
    this.sessions = new Map()
    this.generations = new Map()
    this.transitions = new Map()
    this.closing = false
  }

  async start () {
    for (const profile of this.store.list()) {
      if (!profile.desiredOnline) continue
      const generation = this.#invalidate(profile.identityId)
      this.#resetRetryBudget(profile.identityId)
      void this.#connect(profile, true, generation)
    }
  }

  async close () {
    this.closing = true
    for (const profile of this.store.list()) this.#invalidate(profile.identityId)
    await Promise.allSettled(this.transitions.values())
    const pending = []
    for (const state of this.sessions.values()) {
      if (state.timer) this.timers.clearTimeout(state.timer)
      if (state.session) pending.push(Promise.resolve(state.session.disconnect('runtime shutdown')))
    }
    this.sessions.clear()
    await Promise.allSettled(pending)
  }

  list () { return this.store.list().map(profile => this.#status(profile)) }

  status (name) {
    const profile = this.store.getByName(name)
    if (!profile) throw new ProfileError('not_found', `Bot ${name} does not exist`)
    return this.#status(profile)
  }

  async spawn (name) {
    let profile = this.store.getByName(name)
    let created = false
    if (!profile) {
      profile = this.store.create(name)
      created = true
    }
    const current = this.sessions.get(profile.identityId)
    if (profile.desiredOnline && current && ACTIVE_STATES.has(current.state)) {
      return { ...this.#status(profile), created, alreadyOnline: true }
    }
    profile = this.store.update(profile.identityId, { desiredOnline: true })
    const generation = this.#invalidate(profile.identityId)
    return this.#serialize(profile.identityId, async () => {
      const currentProfile = this.store.getById(profile.identityId)
      if (!this.#mayConnect(currentProfile, generation)) {
        return { ...this.#status(currentProfile), created, alreadyOnline: false }
      }
      if (!await this.#prepareConnect(currentProfile, generation, 'spawn session replacement')) {
        return { ...this.#status(this.store.getById(profile.identityId)), created, alreadyOnline: false }
      }
      this.#resetRetryBudget(profile.identityId)
      const readyProfile = this.store.getById(profile.identityId)
      void this.#connect(readyProfile, false, generation)
      return { ...this.#status(readyProfile), created, alreadyOnline: false }
    })
  }

  async resume (name) {
    let profile = this.#require(name)
    const current = this.sessions.get(profile.identityId)
    if (profile.desiredOnline && current && ACTIVE_STATES.has(current.state)) return { ...this.#status(profile), alreadyOnline: true }
    profile = this.store.update(profile.identityId, { desiredOnline: true })
    const generation = this.#invalidate(profile.identityId)
    return this.#serialize(profile.identityId, async () => {
      const currentProfile = this.store.getById(profile.identityId)
      if (!this.#mayConnect(currentProfile, generation)) {
        return { ...this.#status(currentProfile), alreadyOnline: false }
      }
      if (!await this.#prepareConnect(currentProfile, generation, 'resume session replacement')) {
        return { ...this.#status(this.store.getById(profile.identityId)), alreadyOnline: false }
      }
      this.#resetRetryBudget(profile.identityId)
      const readyProfile = this.store.getById(profile.identityId)
      void this.#connect(readyProfile, false, generation)
      return { ...this.#status(readyProfile), alreadyOnline: false }
    })
  }

  async reconnectBot (name) {
    const profile = this.#require(name)
    if (!profile.desiredOnline) throw new ProfileError('offline', `Bot ${profile.name} is intentionally offline`)
    const generation = this.#invalidate(profile.identityId)
    return this.#serialize(profile.identityId, async () => {
      const currentProfile = this.store.getById(profile.identityId)
      if (!this.#mayConnect(currentProfile, generation)) return this.#status(currentProfile)
      this.#resetRetryBudget(profile.identityId)
      await this.#endCurrent(profile.identityId, 'explicit reconnect', generation)
      if (!this.#mayConnect(this.store.getById(profile.identityId), generation)) {
        return this.#status(this.store.getById(profile.identityId))
      }
      await this.#replacementDelay()
      const replacementProfile = this.store.getById(profile.identityId)
      if (this.#mayConnect(replacementProfile, generation)) {
        void this.#connect(replacementProfile, true, generation)
      }
      return this.#status(replacementProfile)
    })
  }

  async despawn (name) {
    let profile = this.#require(name)
    profile = this.store.update(profile.identityId, { desiredOnline: false })
    const generation = this.#invalidate(profile.identityId)
    return this.#serialize(profile.identityId, async () => {
      await this.#endCurrent(profile.identityId, 'intentional despawn', generation)
      const currentProfile = this.store.getById(profile.identityId)
      if (this.generations.get(profile.identityId) === generation && currentProfile && !currentProfile.desiredOnline) {
        this.sessions.set(profile.identityId, this.#newState('offline', this.sessions.get(profile.identityId)))
      }
      return this.#status(currentProfile)
    })
  }

  async forget (name) {
    const profile = this.#require(name)
    return this.#serialize(profile.identityId, async () => {
      const currentProfile = this.store.getById(profile.identityId)
      const state = this.sessions.get(profile.identityId)
      if (!currentProfile) throw new ProfileError('not_found', `Bot ${name} does not exist`)
      if (currentProfile.desiredOnline || state?.session || state?.timer || (state && ACTIVE_STATES.has(state.state))) {
        throw new ProfileError('online', `Despawn ${currentProfile.name} before forgetting it`)
      }
      this.#invalidate(profile.identityId)
      this.sessions.delete(profile.identityId)
      this.store.remove(profile.identityId)
      return { name: currentProfile.name, identityId: currentProfile.identityId, forgotten: true }
    })
  }

  async rename (name, newName) {
    const profile = this.#require(name)
    validateBotName(newName)
    const collision = this.store.getByName(newName)
    if (collision && collision.identityId !== profile.identityId) {
      throw new ProfileError('name_conflict', `A Bot named ${newName} already exists`)
    }
    const wasOnline = profile.desiredOnline
    const generation = this.#invalidate(profile.identityId)
    return this.#serialize(profile.identityId, async () => {
      if (wasOnline) {
        await this.#endCurrent(profile.identityId, 'rename reconnect', generation)
        if (!this.#mayConnect(this.store.getById(profile.identityId), generation)) {
          return this.#status(this.store.getById(profile.identityId))
        }
        await this.#replacementDelay()
        if (!this.#mayConnect(this.store.getById(profile.identityId), generation)) {
          return this.#status(this.store.getById(profile.identityId))
        }
      }
      let renamed
      try {
        renamed = this.store.update(profile.identityId, { name: newName })
      } catch (error) {
        // An online rename closes the old transport before committing the new
        // login name. If persistence loses a race or fails, keep the persisted
        // desired-online intent operational under whichever profile remains.
        const persisted = this.store.getById(profile.identityId)
        if (wasOnline && this.#mayConnect(persisted, generation) && !this.sessions.get(profile.identityId)?.session) {
          this.#resetRetryBudget(profile.identityId)
          void this.#connect(persisted, true, generation)
        }
        throw error
      }
      if (wasOnline && this.#mayConnect(renamed, generation)) {
        this.#resetRetryBudget(profile.identityId)
        void this.#connect(renamed, true, generation)
      }
      return this.#status(renamed)
    })
  }

  move (name, direction) {
    const { profile, state } = this.#requireOnline(name)
    state.inputs.setMovement(direction)
    state.session.applyInputs(state.inputs)
    return this.#status(profile)
  }

  look (name, yaw, pitch) {
    const { profile, state } = this.#requireOnline(name)
    state.inputs.setLook(yaw, pitch)
    state.session.applyInputs(state.inputs)
    return this.#status(profile)
  }

  action (name, action, mode, intervalTicks) {
    const { profile, state } = this.#requireOnline(name)
    state.inputs.setAction(action, mode, intervalTicks)
    state.session.applyInputs(state.inputs)
    return this.#status(profile)
  }

  flag (name, flag, value) {
    const { profile, state } = this.#requireOnline(name)
    state.inputs.setFlag(flag, value)
    state.session.applyInputs(state.inputs)
    return this.#status(profile)
  }

  hotbar (name, slot) {
    const { profile, state } = this.#requireOnline(name)
    if (slot !== undefined) state.session.selectHotbar(slot - 1)
    return { ...this.#status(profile), selectedHotbarSlot: state.session.selectedHotbarSlot() + 1 }
  }

  interact (name, interaction) {
    const { profile, state } = this.#requireOnline(name)
    state.session.interactBlock(interaction)
    return this.#status(profile)
  }

  drop (name, stack = false) {
    const { profile, state } = this.#requireOnline(name)
    state.session.dropSelected(Boolean(stack))
    return this.#status(profile)
  }

  stop (name) {
    const profile = this.#require(name)
    const state = this.sessions.get(profile.identityId)
    if (!state) throw new ProfileError('not_online', `Bot ${profile.name} has no active session`)
    state.inputs.stopAll()
    state.session?.applyInputs(state.inputs)
    return this.#status(profile)
  }

  #require (name) {
    const profile = this.store.getByName(name)
    if (!profile) throw new ProfileError('not_found', `Bot ${name} does not exist`)
    return profile
  }

  #requireOnline (name) {
    const profile = this.#require(name)
    const state = this.sessions.get(profile.identityId)
    if (!state || state.state !== 'online') throw new ProfileError('not_online', `Bot ${profile.name} is not online`)
    return { profile, state }
  }

  #newState (state, previous = {}) {
    return {
      state,
      session: previous.session,
      inputs: previous.inputs ?? new InputState(),
      reconnectAttempt: previous.reconnectAttempt ?? 0,
      nextReconnectAt: previous.nextReconnectAt ?? null,
      lastError: previous.lastError ?? null,
      timer: previous.timer,
      handledGeneration: previous.handledGeneration
    }
  }

  #serialize (identityId, operation) {
    const previous = this.transitions.get(identityId) ?? Promise.resolve()
    const result = previous.then(operation)
    const tail = result.catch(() => {}).finally(() => {
      if (this.transitions.get(identityId) === tail) this.transitions.delete(identityId)
    })
    this.transitions.set(identityId, tail)
    return result
  }

  #invalidate (identityId) {
    const generation = (this.generations.get(identityId) ?? 0) + 1
    this.generations.set(identityId, generation)
    const state = this.sessions.get(identityId)
    if (state?.timer) {
      this.timers.clearTimeout(state.timer)
      state.timer = undefined
      state.nextReconnectAt = null
    }
    return generation
  }

  #mayConnect (profile, generation) {
    return !this.closing && profile !== undefined && profile.desiredOnline && this.generations.get(profile.identityId) === generation
  }

  #resetRetryBudget (identityId) {
    const state = this.sessions.get(identityId)
    if (!state) return
    state.reconnectAttempt = 0
    state.nextReconnectAt = null
    state.lastError = null
  }

  async #prepareConnect (profile, generation, reason) {
    if (!this.sessions.get(profile.identityId)?.session) return true
    await this.#endCurrent(profile.identityId, reason, generation)
    if (!this.#mayConnect(this.store.getById(profile.identityId), generation)) return false
    await this.#replacementDelay()
    return this.#mayConnect(this.store.getById(profile.identityId), generation)
  }

  #status (profile) {
    const state = this.sessions.get(profile.identityId) ?? this.#newState('offline')
    return {
      identityId: profile.identityId,
      name: profile.name,
      desiredState: profile.desiredOnline ? 'online' : 'offline',
      connectionState: state.state,
      reconnectAttempt: state.reconnectAttempt,
      nextReconnectAt: state.nextReconnectAt,
      lastError: state.lastError,
      inputs: state.inputs.snapshot()
    }
  }

  async #connect (profile, reconnecting, generation) {
    if (!this.#mayConnect(profile, generation)) return
    const previous = this.sessions.get(profile.identityId)
    if (previous?.session) {
      previous.state = 'failed'
      previous.lastError = 'Refusing to open a replacement before the previous session closes'
      this.sessions.set(profile.identityId, previous)
      this.emit('state', this.#status(profile))
      return
    }
    const state = this.#newState(reconnecting ? 'reconnecting' : 'connecting', previous ?? {})
    state.timer = undefined
    state.nextReconnectAt = null
    state.handledGeneration = undefined
    this.sessions.set(profile.identityId, state)
    this.emit('state', this.#status(profile))
    try {
      const session = this.sessionFactory(profile)
      state.session = session
      session.once('close', error => this.#disconnected(profile.identityId, generation, session, error))
      await session.connect()
      const currentProfile = this.store.getById(profile.identityId)
      if (!this.#mayConnect(currentProfile, generation) || this.sessions.get(profile.identityId)?.session !== session) {
        try { await session.disconnect('superseded session') } catch {}
        return
      }
      state.state = 'online'
      state.reconnectAttempt = 0
      state.lastError = null
      session.applyInputs(state.inputs)
      this.emit('state', this.#status(currentProfile))
    } catch (error) {
      this.#disconnected(profile.identityId, generation, state.session, error)
    }
  }

  #disconnected (identityId, generation, session, error) {
    if (this.generations.get(identityId) !== generation) return
    const profile = this.store.getById(identityId)
    if (!profile) return
    const state = this.sessions.get(identityId) ?? this.#newState('offline')
    if (state.session && state.session !== session) return
    if (state.handledGeneration === generation) return
    state.handledGeneration = generation
    state.session = undefined
    // Replaying held input after transport loss is surprising and can trap a
    // malformed interaction in a reconnect loop. Lifecycle desire survives;
    // transient input does not.
    state.inputs.reset()
    state.lastError = error ? String(error.message ?? error) : 'Connection closed unexpectedly'
    if (!profile.desiredOnline) {
      state.state = 'offline'
      this.sessions.set(identityId, state)
      this.emit('state', this.#status(profile))
      return
    }
    state.reconnectAttempt += 1
    if (state.reconnectAttempt > this.reconnect.maximumAttempts) {
      state.state = 'failed'
      state.nextReconnectAt = null
      this.sessions.set(identityId, state)
      this.emit('state', this.#status(profile))
      return
    }
    const delay = Math.min(this.reconnect.maximumDelayMs, this.reconnect.initialDelayMs * (2 ** (state.reconnectAttempt - 1)))
    state.state = 'reconnecting'
    state.nextReconnectAt = new Date(Date.now() + delay).toISOString()
    state.timer = this.timers.setTimeout(() => {
      state.timer = undefined
      void this.#serialize(identityId, async () => {
        const currentProfile = this.store.getById(identityId)
        if (this.#mayConnect(currentProfile, generation)) void this.#connect(currentProfile, true, generation)
      })
    }, delay)
    this.sessions.set(identityId, state)
    this.emit('state', this.#status(profile))
  }

  async #endCurrent (identityId, reason, generation) {
    const state = this.sessions.get(identityId)
    if (!state) return
    if (state.timer) this.timers.clearTimeout(state.timer)
    state.timer = undefined
    state.nextReconnectAt = null
    state.inputs.reset()
    const session = state.session
    if (!session) return
    try {
      await session.disconnect(reason)
    } catch (error) {
      state.state = 'failed'
      state.lastError = `Failed to close session: ${String(error.message ?? error)}`
      this.sessions.set(identityId, state)
      const profile = this.store.getById(identityId)
      if (profile) this.emit('state', this.#status(profile))
      throw new ProfileError('disconnect_failed', state.lastError)
    }
    if (state.session === session) state.session = undefined
    state.state = 'offline'
    state.lastError = null
    state.handledGeneration = generation
  }

  #replacementDelay () {
    if (this.reconnect.sessionReplacementDelayMs === 0) return Promise.resolve()
    return new Promise(resolve => this.timers.setTimeout(resolve, this.reconnect.sessionReplacementDelayMs))
  }
}
