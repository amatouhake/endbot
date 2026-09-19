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
  constructor (profile) { super(); this.profile = profile; this.disconnects = []; this.inputSnapshots = []; this.slot = 0; this.interactions = []; this.drops = [] }
  async connect () {}
  async disconnect (reason) { this.disconnects.push(reason) }
  applyInputs (inputs) { this.inputSnapshots.push(inputs.snapshot()) }
  selectedHotbarSlot () { return this.slot }
  selectHotbar (slot) { this.slot = slot }
  interactBlock (interaction) { this.interactions.push(interaction) }
  dropSelected (stack) { this.drops.push(stack) }
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
    reconnect: { initialDelayMs: 5, maximumDelayMs: 10, maximumAttempts: 2, sessionReplacementDelayMs: 0 },
    ...options
  })
  return { lifecycle, sessions }
}

const turn = () => new Promise(resolve => setImmediate(resolve))
const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))

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

test('hotbar interaction and drop target only the named live session', async () => {
  const { lifecycle, sessions } = fixture()
  await lifecycle.spawn('Alice')
  await lifecycle.spawn('Bob')
  await turn()

  assert.equal(lifecycle.hotbar('Alice', 4).selectedHotbarSlot, 4)
  lifecycle.interact('Alice', { blockPosition: [1, 64, 2], blockRuntimeId: 42, face: 1 })
  lifecycle.drop('Alice', true)

  assert.equal(sessions[0].slot, 3)
  assert.deepEqual(sessions[0].interactions, [{ blockPosition: [1, 64, 2], blockRuntimeId: 42, face: 1 }])
  assert.deepEqual(sessions[0].drops, [true])
  assert.equal(sessions[1].slot, 0)
  assert.deepEqual(sessions[1].interactions, [])
  assert.deepEqual(sessions[1].drops, [])
})

test('unexpected disconnect reconnects with the same identity', async () => {
  const { lifecycle, sessions } = fixture()
  const spawned = await lifecycle.spawn('Alice')
  await turn()
  sessions[0].emit('close', new Error('network gone'))
  sessions[0].emit('close', new Error('duplicate transport close'))
  assert.equal(lifecycle.status('Alice').connectionState, 'reconnecting')
  assert.equal(lifecycle.status('Alice').inputs.movement, null)
  assert.doesNotThrow(() => lifecycle.stop('Alice'))
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

test('rename validates profile-name conflicts before disconnecting', async () => {
  const { lifecycle, sessions } = fixture()
  await lifecycle.spawn('Alice')
  await lifecycle.spawn('Bob')
  await turn()
  await assert.rejects(lifecycle.rename('Alice', 'Bob'), error => error.code === 'name_conflict')
  await assert.rejects(lifecycle.rename('Alice', 'bad-name'), error => error.code === 'invalid_name')
  assert.deepEqual(sessions[0].disconnects, [])
  assert.equal(lifecycle.status('Alice').connectionState, 'online')
})

test('session replacement waits for the old transport to close', async () => {
  let releaseDisconnect
  class DelayedDisconnectSession extends FakeSession {
    async disconnect (reason) {
      this.disconnects.push(reason)
      await new Promise(resolve => { releaseDisconnect = resolve })
    }
  }
  const { lifecycle, sessions } = fixture({
    sessionFactory: profile => {
      const session = new DelayedDisconnectSession(profile)
      sessions.push(session)
      return session
    }
  })
  await lifecycle.spawn('Alice')
  await turn()

  const reconnect = lifecycle.reconnectBot('Alice')
  await turn()
  assert.equal(sessions.length, 1)
  releaseDisconnect()
  await reconnect
  await turn()
  assert.equal(sessions.length, 2)
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

test('despawn during replacement delay cancels reconnect and permits forget', async () => {
  const { lifecycle, sessions } = fixture({
    reconnect: { initialDelayMs: 5, maximumDelayMs: 10, maximumAttempts: 2, sessionReplacementDelayMs: 30 }
  })
  await lifecycle.spawn('Alice')
  await turn()

  const reconnect = lifecycle.reconnectBot('Alice')
  await turn()
  const despawn = lifecycle.despawn('Alice')
  await Promise.all([reconnect, despawn])
  await wait(40)

  assert.equal(sessions.length, 1)
  assert.equal(lifecycle.status('Alice').desiredState, 'offline')
  assert.equal(lifecycle.status('Alice').connectionState, 'offline')
  assert.equal((await lifecycle.forget('Alice')).forgotten, true)
  await wait(40)
  assert.equal(sessions.length, 1)
  assert.throws(() => lifecycle.status('Alice'), error => error.code === 'not_found')
})

test('disconnect failure is observable, retryable, and does not start a replacement', async () => {
  let failures = 1
  class RetryableDisconnectSession extends FakeSession {
    async disconnect (reason) {
      this.disconnects.push(reason)
      if (failures-- > 0) throw new Error('transport close timed out')
    }
  }
  const { lifecycle, sessions } = fixture({
    sessionFactory: profile => {
      const session = new RetryableDisconnectSession(profile)
      sessions.push(session)
      return session
    }
  })
  await lifecycle.spawn('Alice')
  await turn()

  await assert.rejects(lifecycle.rename('Alice', 'Builder'), error => error.code === 'disconnect_failed')
  assert.equal(sessions.length, 1)
  assert.equal(lifecycle.status('Alice').connectionState, 'failed')
  assert.match(lifecycle.status('Alice').lastError, /transport close timed out/)
  assert.throws(() => lifecycle.status('Builder'), error => error.code === 'not_found')

  const renamed = await lifecycle.rename('Alice', 'Builder')
  await turn()
  assert.equal(renamed.identityId, lifecycle.status('Builder').identityId)
  assert.equal(lifecycle.status('Builder').connectionState, 'online')
  assert.equal(sessions.length, 2)
})

test('despawn disconnect failure remains forget-safe and can be retried', async () => {
  let failures = 1
  class RetryableDisconnectSession extends FakeSession {
    async disconnect (reason) {
      this.disconnects.push(reason)
      if (failures-- > 0) throw new Error('close rejected')
    }
  }
  const { lifecycle, sessions } = fixture({
    sessionFactory: profile => {
      const session = new RetryableDisconnectSession(profile)
      sessions.push(session)
      return session
    }
  })
  await lifecycle.spawn('Alice')
  await turn()

  await assert.rejects(lifecycle.despawn('Alice'), error => error.code === 'disconnect_failed')
  assert.equal(lifecycle.status('Alice').desiredState, 'offline')
  assert.equal(lifecycle.status('Alice').connectionState, 'failed')
  await assert.rejects(lifecycle.forget('Alice'), error => error.code === 'online')

  await lifecycle.despawn('Alice')
  assert.equal(lifecycle.status('Alice').connectionState, 'offline')
  assert.equal((await lifecycle.forget('Alice')).forgotten, true)
})

test('resume after failed despawn closes the retained session before replacing it', async () => {
  let failures = 1
  class RetryableDisconnectSession extends FakeSession {
    async disconnect (reason) {
      this.disconnects.push(reason)
      if (failures-- > 0) throw new Error('close rejected')
    }
  }
  const { lifecycle, sessions } = fixture({
    sessionFactory: profile => {
      const session = new RetryableDisconnectSession(profile)
      sessions.push(session)
      return session
    }
  })
  await lifecycle.spawn('Alice')
  await turn()
  await assert.rejects(lifecycle.despawn('Alice'), error => error.code === 'disconnect_failed')

  await lifecycle.resume('Alice')
  await turn()
  assert.equal(sessions.length, 2)
  assert.equal(sessions[0].disconnects.length, 2)
  assert.equal(lifecycle.status('Alice').connectionState, 'online')
})

test('operator resume resets an exhausted automatic reconnect budget', async () => {
  let connectAttempts = 0
  class FailingSession extends FakeSession {
    async connect () {
      connectAttempts += 1
      if (connectAttempts <= 4) throw new Error(`connect failure ${connectAttempts}`)
    }
  }
  const { lifecycle } = fixture({
    sessionFactory: profile => new FailingSession(profile),
    reconnect: { initialDelayMs: 3, maximumDelayMs: 3, maximumAttempts: 2, sessionReplacementDelayMs: 0 }
  })
  await lifecycle.spawn('Alice')
  await wait(20)
  assert.equal(lifecycle.status('Alice').connectionState, 'failed')
  assert.equal(lifecycle.status('Alice').reconnectAttempt, 3)

  await lifecycle.resume('Alice')
  await turn()
  assert.equal(lifecycle.status('Alice').connectionState, 'reconnecting')
  assert.equal(lifecycle.status('Alice').reconnectAttempt, 1)
  await wait(10)
  assert.equal(lifecycle.status('Alice').connectionState, 'online')
  assert.equal(lifecycle.status('Alice').reconnectAttempt, 0)
})
