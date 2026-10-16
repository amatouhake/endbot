// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import test from 'node:test'

import { InputState } from '../src/actions.js'

test('timed use expires after exactly N input ticks without stopping other inputs', () => {
  const inputs = new InputState()
  inputs.setMovement('forward')
  inputs.setAction('attack', 'continuous')
  inputs.setAction('use', 'continuous', undefined, 3)
  assert.equal(inputs.snapshot().actions.use.remainingTicks, 3)
  for (let blocked = 0; blocked < 2; blocked++) {
    inputs.step()
    assert.equal(inputs.snapshot().actions.use.remainingTicks, 3)
  }
  for (let tick = 0; tick < 3; tick++) {
    assert.deepEqual(inputs.step().triggered, ['attack', 'use'])
    inputs.startTimedUse(inputs.actionRevision('use'))
  }
  assert.deepEqual(inputs.step().triggered, ['attack'])
  assert.equal(inputs.snapshot().movement, 'forward')
  assert.equal(inputs.actionMode('use'), undefined)
  for (const duration of [0, 72001, 1.5, '20', true]) {
    assert.throws(() => inputs.setAction('use', 'continuous', undefined, duration), /Duration/)
  }
  assert.throws(() => inputs.setAction('jump', 'once', undefined, 20), /Duration/)
  assert.throws(() => inputs.setAction('jump', 'stop', undefined, 20), /Duration/)
})

test('for counts from acceptance for jump attack and interval use', () => {
  const inputs = new InputState()
  inputs.setAction('jump', 'continuous', undefined, 3)
  inputs.setAction('use', 'interval', 2, 4)
  assert.equal(inputs.snapshot().actions.jump.remainingTicks, 3)
  const triggered = []
  for (let tick = 0; tick < 6; tick++) triggered.push(inputs.step().triggered.sort().join('+'))
  assert.deepEqual(triggered, ['jump+use', 'jump', 'jump+use', '', '', ''])
  assert.deepEqual(inputs.snapshot().actions, {})
})

test('a new command replaces the previous deadline', () => {
  const inputs = new InputState()
  inputs.setAction('attack', 'continuous', undefined, 2)
  inputs.step()
  inputs.setAction('attack', 'interval', 1, 3)
  for (let tick = 0; tick < 3; tick++) assert.deepEqual(inputs.step().triggered, ['attack'])
  assert.deepEqual(inputs.step().triggered, [])
  inputs.setAction('attack', 'continuous', undefined, 5)
  inputs.setAction('attack', 'continuous')
  assert.equal(inputs.snapshot().actions.attack.remainingTicks, undefined)
})

test('timed movement stops only movement after N ticks', () => {
  const inputs = new InputState()
  inputs.setAction('attack', 'continuous')
  inputs.setMovement('forward', 2)
  assert.equal(inputs.snapshot().movementRemainingTicks, 2)
  assert.equal(inputs.step().movement, 'forward')
  assert.equal(inputs.step().movement, 'forward')
  const expired = inputs.step()
  assert.equal(expired.movement, undefined)
  assert.deepEqual(expired.triggered, ['attack'])
  assert.equal(inputs.snapshot().movementRemainingTicks, undefined)
  inputs.setMovement('left', 5)
  inputs.setMovement('right')
  assert.equal(inputs.snapshot().movementRemainingTicks, undefined)
  for (const duration of [0, 72001, 1.5]) assert.throws(() => inputs.setMovement('forward', duration), /duration/)
  assert.throws(() => inputs.setMovement('stop', 10), /duration/)
})

test('use hold schedules presses that the session releases', () => {
  const inputs = new InputState()
  inputs.setAction('use', 'once', undefined, undefined, 3)
  const revision = inputs.actionRevision('use')
  assert.deepEqual(inputs.step().triggered, ['use'])
  assert.deepEqual(inputs.step().triggered, [])
  assert.equal(inputs.actionMode('use'), 'once')
  assert.equal(inputs.holdTicks('use'), 3)
  assert.equal(inputs.hasTimedUse(), true)
  inputs.finishHold(revision + 1)
  assert.equal(inputs.actionMode('use'), 'once')
  inputs.finishHold(revision)
  assert.equal(inputs.actionMode('use'), undefined)

  inputs.setAction('use', 'continuous', undefined, 10, 3)
  const presses = []
  for (let tick = 1; tick <= 10; tick++) if (inputs.step().triggered.includes('use')) presses.push(tick)
  assert.deepEqual(presses, [1, 5, 9])
  inputs.finishHold(inputs.actionRevision('use'))
  assert.equal(inputs.snapshot().actions.use.holdTicks, 3)
  inputs.step()
  assert.equal(inputs.actionMode('use'), undefined)

  inputs.setAction('use', 'interval', 8, undefined, 3)
  assert.equal(inputs.snapshot().actions.use.intervalTicks, 8)
  assert.throws(() => inputs.setAction('use', 'interval', 3, undefined, 3), /longer than hold/)
  assert.throws(() => inputs.setAction('jump', 'once', undefined, undefined, 3), /Hold/)
  assert.throws(() => inputs.setAction('use', 'once', undefined, undefined, 0), /Hold/)
  inputs.cancelTimedUse()
  assert.equal(inputs.actionMode('use'), undefined)
})

test('once, continuous, interval, and stop share one scheduler', () => {
  const inputs = new InputState()
  inputs.setAction('jump', 'once')
  inputs.setAction('attack', 'continuous')
  inputs.setAction('use', 'interval', 3)
  assert.deepEqual(inputs.step().triggered.sort(), ['attack', 'jump', 'use'])
  assert.deepEqual(inputs.step().triggered, ['attack'])
  assert.deepEqual(inputs.step().triggered, ['attack'])
  assert.deepEqual(inputs.step().triggered.sort(), ['attack', 'use'])
  inputs.setAction('attack', 'stop')
  assert.deepEqual(inputs.step().triggered, [])
})

test('global stop clears movement actions sprint and sneak but not look', () => {
  const inputs = new InputState()
  inputs.setMovement('forward')
  inputs.setFlag('sprint', true)
  inputs.setFlag('sneak', true)
  inputs.setLook(450, -20)
  inputs.setAction('attack', 'continuous')
  inputs.stopAll()
  assert.deepEqual(inputs.snapshot(), {
    movement: null, sprint: false, sneak: false, look: { yaw: 90, pitch: -20 }, actions: {}
  })
})

test('sprint and sneak transitions are emitted once and reset clears stale transitions', () => {
  const inputs = new InputState()
  inputs.setFlag('sprint', true)
  inputs.setFlag('sneak', true)
  assert.deepEqual(inputs.step().transitions, ['start_sprint', 'start_sneak'])
  assert.deepEqual(inputs.step().transitions, [])
  inputs.setFlag('sprint', false)
  inputs.setFlag('sneak', false)
  assert.deepEqual(inputs.step().transitions, ['stop_sprint', 'stop_sneak'])
  inputs.setFlag('sprint', true)
  inputs.reset()
  assert.deepEqual(inputs.step().transitions, [])
  assert.equal(inputs.snapshot().sprint, false)
})
