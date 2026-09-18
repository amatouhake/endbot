// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { BotLifecycle } from '../src/lifecycle.js'
import { ProfileStore } from '../src/profile-store.js'

class FakeSession extends EventEmitter {
  constructor (profile) { super(); this.profile = profile; this.disconnects = []; this.inputSnapshots = [] }
  async connect () {}
  async disconnect (reason) { this.disconnects.push(reason) }
  applyInputs (inputs) { this.inputSnapshots.push(inputs.snapshot()) }
}

function fixture (options = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-lifecycle-'))
  test.after(() => fs.rmSync(directory, { recursive: true }))
  const sessions = []
  const lifecycle = new BotLifecycle({
    store: new ProfileStore(directory),
    sessionFactory: profile => {
      const session = new FakeSession(profile)
      sessions.push(session)
      return session
    },
    reconnect: { initialDelayMs: 5, maximumDelayMs: 10, maximumAttempts: 2 },
    ...options
  })
  return { lifecycle, sessions }
}

const turn = () => new Promise(resolve => setImmediate(resolve))

test('implicit spawn creates once, isolates multiple bots, and despawn disables reconnect', async () => {
  const { lifecycle, sessions } = fixture()
  const alice = await lifecycle.spawn('Alice')
  const bob = await lifecycle.spawn('Bob')
  await turn()
  assert.notEqual(alice.identityId, bob.identityId)
  assert.equal(lifecycle.status('Alice').connectionState, 'online')
  lifecycle.move('Alice', 'forward')
  assert.equal(lifecycle.status('Alice').inputs.movement, 'forward')
  assert.equal(lifecycle.status('Bob').inputs.movement, null)
  await lifecycle.despawn('Alice')
  sessions[0].emit('close', new Error('late close'))
  assert.equal(lifecycle.status('Alice').desiredState, 'offline')
  assert.equal(lifecycle.status('Alice').connectionState, 'offline')
})

test('unexpected disconnect reconnects with the same identity', async () => {
  const { lifecycle, sessions } = fixture()
  const spawned = await lifecycle.spawn('Alice')
  await turn()
  sessions[0].emit('close', new Error('network gone'))
  sessions[0].emit('close', new Error('duplicate transport close'))
  assert.equal(lifecycle.status('Alice').connectionState, 'reconnecting')
  await new Promise(resolve => setTimeout(resolve, 15))
  assert.equal(sessions.length, 2)
  assert.equal(sessions[1].profile.identityId, spawned.identityId)
  assert.equal(lifecycle.status('Alice').connectionState, 'online')
})

test('rename preserves identity, updates lookup, and reconnects an online bot', async () => {
  const { lifecycle, sessions } = fixture()
  const spawned = await lifecycle.spawn('Alice')
  await turn()
  const renamed = await lifecycle.rename('Alice', 'Builder')
  await turn()
  assert.equal(renamed.identityId, spawned.identityId)
  assert.throws(() => lifecycle.status('Alice'), error => error.code === 'not_found')
  assert.equal(lifecycle.status('Builder').connectionState, 'online')
  assert.equal(sessions[0].disconnects[0], 'rename reconnect')
  assert.equal(sessions[1].profile.name, 'Builder')
})

test('forget fails safe for live bots and releases an offline name', async () => {
  const { lifecycle } = fixture()
  await lifecycle.spawn('Alice')
  await turn()
  await assert.rejects(lifecycle.forget('Alice'), error => error.code === 'online')
  await lifecycle.despawn('Alice')
  const result = await lifecycle.forget('Alice')
  assert.equal(result.forgotten, true)
  assert.equal((await lifecycle.spawn('Alice')).created, true)
})
