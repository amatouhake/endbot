// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import { EventEmitter } from 'node:events'

import { InputState } from './actions.js'
import { ProfileError } from './profile-store.js'

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
      maximumAttempts: reconnect.maximumAttempts ?? 8
    }
    this.sessions = new Map()
    this.generations = new Map()
  }

  async start () {
    for (const profile of this.store.list()) {
      if (profile.desiredOnline) void this.#connect(profile, true)
    }
  }

  async close () {
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
    if (current && ACTIVE_STATES.has(current.state)) {
      return { ...this.#status(profile), created, alreadyOnline: true }
    }
    profile = this.store.update(profile.identityId, { desiredOnline: true })
    void this.#connect(profile, false)
    return { ...this.#status(profile), created, alreadyOnline: false }
  }

  async resume (name) {
    let profile = this.#require(name)
    const current = this.sessions.get(profile.identityId)
    if (current && ACTIVE_STATES.has(current.state)) return { ...this.#status(profile), alreadyOnline: true }
    profile = this.store.update(profile.identityId, { desiredOnline: true })
    void this.#connect(profile, false)
    return { ...this.#status(profile), alreadyOnline: false }
  }

  async reconnectBot (name) {
    const profile = this.#require(name)
    if (!profile.desiredOnline) throw new ProfileError('offline', `Bot ${profile.name} is intentionally offline`)
    await this.#endCurrent(profile.identityId, 'explicit reconnect')
    void this.#connect(this.store.getById(profile.identityId), true)
    return this.#status(this.store.getById(profile.identityId))
  }

  async despawn (name) {
    let profile = this.#require(name)
    profile = this.store.update(profile.identityId, { desiredOnline: false })
    await this.#endCurrent(profile.identityId, 'intentional despawn')
    this.sessions.set(profile.identityId, this.#newState('offline'))
    return this.#status(profile)
  }

  async forget (name) {
    const profile = this.#require(name)
    const state = this.sessions.get(profile.identityId)
    if (profile.desiredOnline || (state && ACTIVE_STATES.has(state.state))) {
      throw new ProfileError('online', `Despawn ${profile.name} before forgetting it`)
    }
    this.sessions.delete(profile.identityId)
    this.store.remove(profile.identityId)
    return { name: profile.name, identityId: profile.identityId, forgotten: true }
  }

  async rename (name, newName) {
    let profile = this.#require(name)
    const wasOnline = profile.desiredOnline
    profile = this.store.update(profile.identityId, { name: newName })
    if (wasOnline) {
      await this.#endCurrent(profile.identityId, 'rename reconnect')
      void this.#connect(profile, true)
    }
    return this.#status(profile)
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

  stop (name) {
    const { profile, state } = this.#requireOnline(name)
    state.inputs.stopAll()
    state.session.applyInputs(state.inputs)
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
      timer: previous.timer
    }
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

  async #connect (profile, reconnecting) {
    const generation = (this.generations.get(profile.identityId) ?? 0) + 1
    this.generations.set(profile.identityId, generation)
    const previous = this.sessions.get(profile.identityId)
    const state = this.#newState(reconnecting ? 'reconnecting' : 'connecting', previous ?? {})
    state.timer = undefined
    state.nextReconnectAt = null
    this.sessions.set(profile.identityId, state)
    this.emit('state', this.#status(profile))
    try {
      const session = this.sessionFactory(profile)
      state.session = session
      session.once('close', error => this.#disconnected(profile.identityId, generation, error))
      await session.connect()
      if (this.generations.get(profile.identityId) !== generation) {
        await session.disconnect('superseded session')
        return
      }
      state.state = 'online'
      state.reconnectAttempt = 0
      state.lastError = null
      session.applyInputs(state.inputs)
      this.emit('state', this.#status(this.store.getById(profile.identityId)))
    } catch (error) {
      this.#disconnected(profile.identityId, generation, error)
    }
  }

  #disconnected (identityId, generation, error) {
    if (this.generations.get(identityId) !== generation) return
    const profile = this.store.getById(identityId)
    if (!profile) return
    const state = this.sessions.get(identityId) ?? this.#newState('offline')
    state.session = undefined
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
      void this.#connect(this.store.getById(identityId), true)
    }, delay)
    this.sessions.set(identityId, state)
    this.emit('state', this.#status(profile))
  }

  async #endCurrent (identityId, reason) {
    this.generations.set(identityId, (this.generations.get(identityId) ?? 0) + 1)
    const state = this.sessions.get(identityId)
    if (!state) return
    if (state.timer) this.timers.clearTimeout(state.timer)
    if (state.session) await state.session.disconnect(reason)
    state.session = undefined
    state.timer = undefined
  }
}
