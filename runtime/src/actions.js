// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

const ACTIONS = new Set(['jump', 'attack', 'use'])
const DIRECTIONS = new Set(['forward', 'backward', 'left', 'right'])

function validTicks (value) {
  return Number.isSafeInteger(value) && value >= 1 && value <= 72_000
}

export class InputState {
  constructor () {
    this.tick = 0
    this.actions = new Map()
    this.actionSequence = 0
    this.movement = undefined
    this.movementEndTick = undefined
    this.sprint = false
    this.sneak = false
    this.transitions = []
    this.yaw = 0
    this.pitch = 0
  }

  // durationTicks (`for`) runs from command acceptance, except a plain held
  // `use continuous`: that legacy timed use starts at its first air use so a
  // deferred press cannot eat the charge. holdTicks presses use for that many
  // transactions per trigger; the session releases it and calls finishHold.
  setAction (action, mode = 'once', intervalTicks = undefined, durationTicks = undefined, holdTicks = undefined) {
    if (!ACTIONS.has(action)) throw new Error(`Unsupported action: ${action}`)
    if (!['once', 'continuous', 'interval', 'stop'].includes(mode)) throw new Error(`Unsupported action mode: ${mode}`)
    if (durationTicks !== undefined && (mode === 'once' || mode === 'stop' || !validTicks(durationTicks))) {
      throw new Error('Duration requires continuous or interval and an integer from 1 to 72000 ticks')
    }
    if (holdTicks !== undefined && (action !== 'use' || mode === 'stop' || !validTicks(holdTicks))) {
      throw new Error('Hold requires use and an integer from 1 to 72000 ticks')
    }
    if (mode === 'stop') {
      this.actions.delete(action)
      return
    }
    let interval = mode === 'continuous' ? 1 : mode === 'once' ? 0 : Number(intervalTicks)
    if (!Number.isSafeInteger(interval) || interval < 0 || interval > 72_000) {
      throw new Error('Action interval must be between 1 and 72000 ticks')
    }
    if (mode === 'interval' && interval < 1) throw new Error('Action interval must be at least one tick')
    if (holdTicks !== undefined) {
      // A release needs its own tick before the next press.
      if (mode === 'continuous') interval = holdTicks + 1
      else if (mode === 'interval' && interval <= holdTicks) throw new Error('Use interval must be longer than hold')
    }
    const legacyTimedUse = durationTicks !== undefined && action === 'use' && mode === 'continuous' &&
      holdTicks === undefined
    this.actions.set(action, { mode, interval, nextTick: this.tick + 1, revision: ++this.actionSequence,
      ...(legacyTimedUse ? { durationTicks } : {}),
      ...(durationTicks !== undefined && !legacyTimedUse ? { endTick: this.tick + 1 + durationTicks } : {}),
      ...(holdTicks === undefined ? {} : { holdTicks }) })
  }

  actionMode (action) {
    return this.actions.get(action)?.mode
  }

  actionRevision (action) { return this.actions.get(action)?.revision }

  holdTicks (action) { return this.actions.get(action)?.holdTicks }

  // Timed uses end with their held item: a legacy timed hold or any hold mode.
  hasTimedUse () {
    const state = this.actions.get('use')
    return state?.durationTicks !== undefined || state?.holdTicks !== undefined
  }

  startTimedUse (revision) {
    const state = this.actions.get('use')
    if (state && state.revision === revision && state.durationTicks !== undefined && state.endTick === undefined) {
      // Called on the first air-use transaction, after step() selected this
      // tick's inputs. Waiting for another transaction must not eat the hold.
      state.endTick = this.tick + state.durationTicks
    }
  }

  // A one-shot hold stays scheduled (but untriggerable) until released, so
  // its revision keeps identifying the press. Repeating holds keep running.
  finishHold (revision) {
    const state = this.actions.get('use')
    if (state?.revision === revision && state.holdTicks !== undefined && state.mode === 'once') {
      this.actions.delete('use')
    }
  }

  cancelTimedUse () {
    if (this.hasTimedUse()) this.actions.delete('use')
  }

  setMovement (direction, durationTicks = undefined) {
    if (durationTicks !== undefined && (direction === 'stop' || !validTicks(durationTicks))) {
      throw new Error('Movement duration requires a direction and an integer from 1 to 72000 ticks')
    }
    if (direction === 'stop') this.movement = undefined
    else if (!DIRECTIONS.has(direction)) throw new Error(`Unsupported movement direction: ${direction}`)
    else this.movement = direction
    this.movementEndTick = this.movement && durationTicks !== undefined ? this.tick + 1 + durationTicks : undefined
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
    this.setMovement('stop')
    this.setFlag('sprint', false)
    this.setFlag('sneak', false)
  }

  reset () {
    this.actions.clear()
    this.setMovement('stop')
    this.sprint = false
    this.sneak = false
    this.transitions = []
  }

  step () {
    this.tick += 1
    const triggered = []
    if (this.movementEndTick !== undefined && this.tick >= this.movementEndTick) this.setMovement('stop')
    for (const [action, state] of this.actions) {
      if (state.endTick !== undefined && this.tick >= state.endTick) {
        this.actions.delete(action)
        continue
      }
      if (this.tick < state.nextTick) continue
      triggered.push(action)
      if (state.mode === 'once' && state.holdTicks !== undefined) state.nextTick = Infinity
      else if (state.mode === 'once') this.actions.delete(action)
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
      ...(this.movementEndTick === undefined ? {} : { movementRemainingTicks: this.movementEndTick - this.tick - 1 }),
      sprint: this.sprint,
      sneak: this.sneak,
      look: { yaw: this.yaw, pitch: this.pitch },
      actions: Object.fromEntries([...this.actions].map(([name, state]) => [name, {
        mode: state.mode, intervalTicks: state.interval || null,
        ...(state.holdTicks === undefined ? {} : { holdTicks: state.holdTicks }),
        ...(state.durationTicks === undefined && state.endTick === undefined ? {} : {
          remainingTicks: state.endTick === undefined ? state.durationTicks : state.endTick - this.tick - 1
        })
      }]))
    }
  }
}
