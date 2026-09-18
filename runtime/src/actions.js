// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

const ACTIONS = new Set(['jump', 'attack', 'use'])
const DIRECTIONS = new Set(['forward', 'backward', 'left', 'right'])

export class InputState {
  constructor () {
    this.tick = 0
    this.actions = new Map()
    this.movement = undefined
    this.sprint = false
    this.sneak = false
    this.transitions = []
    this.yaw = 0
    this.pitch = 0
  }

  setAction (action, mode = 'once', intervalTicks = undefined) {
    if (!ACTIONS.has(action)) throw new Error(`Unsupported action: ${action}`)
    if (!['once', 'continuous', 'interval', 'stop'].includes(mode)) throw new Error(`Unsupported action mode: ${mode}`)
    if (mode === 'stop') {
      this.actions.delete(action)
      return
    }
    const interval = mode === 'continuous' ? 1 : mode === 'once' ? 0 : Number(intervalTicks)
    if (!Number.isSafeInteger(interval) || interval < 0 || interval > 72_000) {
      throw new Error('Action interval must be between 1 and 72000 ticks')
    }
    if (mode === 'interval' && interval < 1) throw new Error('Action interval must be at least one tick')
    this.actions.set(action, { mode, interval, nextTick: this.tick + 1 })
  }

  setMovement (direction) {
    if (direction === 'stop') this.movement = undefined
    else if (!DIRECTIONS.has(direction)) throw new Error(`Unsupported movement direction: ${direction}`)
    else this.movement = direction
  }

  setLook (yaw, pitch) {
    yaw = Number(yaw)
    pitch = Number(pitch)
    if (!Number.isFinite(yaw) || !Number.isFinite(pitch) || pitch < -90 || pitch > 90) {
      throw new Error('Look requires a finite yaw and pitch between -90 and 90')
    }
    this.yaw = ((yaw % 360) + 360) % 360
    this.pitch = pitch
  }

  setFlag (flag, value) {
    if (!['sprint', 'sneak'].includes(flag) || typeof value !== 'boolean') throw new Error('Invalid input flag')
    if (this[flag] === value) return
    this[flag] = value
    this.transitions.push(`${value ? 'start' : 'stop'}_${flag}`)
  }

  stopAll () {
    this.actions.clear()
    this.movement = undefined
    this.setFlag('sprint', false)
    this.setFlag('sneak', false)
  }

  reset () {
    this.actions.clear()
    this.movement = undefined
    this.sprint = false
    this.sneak = false
    this.transitions = []
  }

  step () {
    this.tick += 1
    const triggered = []
    for (const [action, state] of this.actions) {
      if (this.tick < state.nextTick) continue
      triggered.push(action)
      if (state.mode === 'once') this.actions.delete(action)
      else state.nextTick = this.tick + state.interval
    }
    return {
      tick: this.tick,
      triggered,
      transitions: this.transitions.splice(0),
      movement: this.movement,
      sprint: this.sprint,
      sneak: this.sneak,
      yaw: this.yaw,
      pitch: this.pitch
    }
  }

  snapshot () {
    return {
      movement: this.movement ?? null,
      sprint: this.sprint,
      sneak: this.sneak,
      look: { yaw: this.yaw, pitch: this.pitch },
      actions: Object.fromEntries([...this.actions].map(([name, state]) => [name, { mode: state.mode, intervalTicks: state.interval || null }]))
    }
  }
}
