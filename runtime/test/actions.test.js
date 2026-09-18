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
