// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import test from 'node:test'

import { InputState } from '../src/actions.js'

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
