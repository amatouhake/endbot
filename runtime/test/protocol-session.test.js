// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { BedrockSession } from '../src/protocol-session.js'

const turn = () => new Promise(resolve => setImmediate(resolve))

function connectedSession (directory, client) {
  const session = new BedrockSession(
    { name: 'Alice', identityId: '00000000-0000-4000-8000-000000000001' },
    {
      protocolLoader: async () => ({ createClient: () => client }),
      ownerPrivateKeyPath: path.join(directory, 'owner-private.pem'),
      ownerPublicKeyPath: path.join(directory, 'owner-public.pem'),
      serverIdentityPinPath: path.join(directory, 'server.pin'),
      localAuthIssuer: 'endbot-test',
      localAuthAudience: 'endstone-test',
      serverHost: 'localhost',
      serverPort: 19132,
      gameVersion: '1.26.51',
      connectTimeoutMs: 1000
    }
  )
  return { session, connecting: session.connect() }
}

test('disconnect waits for paused initialization and prevents late client creation', async () => {
  let releaseProtocol
  let createClientCalls = 0
  const protocolLoaded = new Promise(resolve => { releaseProtocol = resolve })
  const session = new BedrockSession(
    { name: 'Alice', identityId: '00000000-0000-4000-8000-000000000001' },
    {
      protocolLoader: () => protocolLoaded
    }
  )

  const connecting = session.connect()
  await turn()
  const disconnecting = session.disconnect('superseded before client creation')
  let disconnectSettled = false
  void disconnecting.then(() => { disconnectSettled = true })
  await turn()
  assert.equal(disconnectSettled, false)

  releaseProtocol({
    createClient: () => {
      createClientCalls += 1
      return new EventEmitter()
    }
  })

  await assert.rejects(connecting, /connection was canceled/)
  await disconnecting
  assert.equal(createClientCalls, 0)
  assert.equal(session.client, undefined)
})

test('a rejected disconnect can be retried without reusing the rejected promise', async () => {
  const client = new EventEmitter()
  let attempts = 0
  client.disconnect = () => {
    attempts += 1
    if (attempts === 1) throw new Error('first close failed')
    queueMicrotask(() => client.emit('close'))
  }
  client.close = () => {}
  const session = new BedrockSession({ name: 'Alice', identityId: '00000000-0000-4000-8000-000000000001' }, {})
  session.client = client

  await assert.rejects(session.disconnect('first'), /first close failed/)
  await session.disconnect('retry')
  assert.equal(attempts, 2)
})

test('a terminal transport close after disconnect failure makes retry idempotent', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const client = new EventEmitter()
  let attempts = 0
  client.queue = () => {}
  client.disconnect = () => {
    attempts += 1
    throw new Error('first close failed')
  }
  client.close = () => {}
  const { session, connecting } = connectedSession(directory, client)
  await turn()
  client.emit('spawn')
  await connecting

  await assert.rejects(session.disconnect('first'), /first close failed/)
  client.emit('close', 'transport completed closure')
  await session.disconnect('retry after close')

  assert.equal(attempts, 1)
})

test('client errors retain session ownership until transport closure', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-protocol-session-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const client = new EventEmitter()
  const closeRequests = []
  client.queue = () => {}
  client.disconnect = () => {}
  client.close = reason => closeRequests.push(reason)
  const { session, connecting } = connectedSession(directory, client)
  await turn()
  client.emit('spawn')
  await connecting

  const closures = []
  session.on('close', error => closures.push(error))
  client.emit('error', new Error('recoverable parser failure'))

  assert.deepEqual(closeRequests, ['Endbot protocol error'])
  assert.equal(closures.length, 0)
  client.emit('error', new Error('duplicate parser failure'))
  assert.deepEqual(closeRequests, ['Endbot protocol error'])

  client.emit('close', 'protocol failure shutdown')
  assert.equal(closures.length, 1)
  assert.match(closures[0].message, /recoverable parser failure/)
})
