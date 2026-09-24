// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import net from 'node:net'
import test from 'node:test'

import { ControlServer } from '../src/control-server.js'

function request (port, body) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host: '127.0.0.1', port })
    let response = ''
    socket.on('connect', () => socket.write(`${JSON.stringify(body)}\n`))
    socket.on('data', chunk => { response += chunk })
    socket.on('end', () => resolve(JSON.parse(response)))
    socket.on('error', reject)
  })
}

test('loopback control protocol authenticates and dispatches without exposing tokens', async () => {
  const calls = []
  const lifecycle = { spawn: name => { calls.push(name); return { name } } }
  const token = 'a'.repeat(43)
  const server = new ControlServer({ host: '127.0.0.1', port: 0, token, lifecycle })
  await server.listen()
  test.after(() => server.close())
  const unauthorized = await request(server.port, { version: 1, id: '1', token: 'wrong', method: 'spawn', params: { name: 'Alice' } })
  assert.equal(unauthorized.error.code, 'unauthorized')
  assert.deepEqual(calls, [])
  const accepted = await request(server.port, { version: 1, id: '2', token, method: 'spawn', params: { name: 'Alice' } })
  assert.deepEqual(accepted.result, { name: 'Alice' })
  assert.deepEqual(calls, ['Alice'])
})

test('control server refuses non-loopback binds', () => {
  assert.throws(() => new ControlServer({ host: '0.0.0.0', token: 'a'.repeat(43), lifecycle: {} }), /loopback/)
})

test('default control deadline permits a normal replacement operation', async () => {
  const token = 'a'.repeat(43)
  const lifecycle = {
    reconnectBot: async name => {
      await new Promise(resolve => setTimeout(resolve, 1100))
      return { name, connectionState: 'reconnecting' }
    }
  }
  const server = new ControlServer({ host: '127.0.0.1', port: 0, token, lifecycle })
  await server.listen()
  test.after(() => server.close())
  const response = await request(server.port, {
    version: 1,
    id: 'replacement',
    token,
    method: 'reconnect',
    params: { name: 'Alice' }
  })
  assert.equal(response.ok, true)
  assert.equal(response.result.connectionState, 'reconnecting')
})

test('shutdown answers { stopping: true } then runs the shutdown hook', async () => {
  const token = 'a'.repeat(43)
  const calls = []
  let notifyHooked
  const hooked = new Promise(resolve => { notifyHooked = resolve })
  const lifecycle = { close: async () => { calls.push('close') } }
  const server = new ControlServer({
    host: '127.0.0.1',
    port: 0,
    token,
    lifecycle,
    onShutdown: () => {
      calls.push('shutdown')
      return lifecycle.close().then(notifyHooked)
    }
  })
  await server.listen()
  test.after(() => server.close())
  const response = await request(server.port, { version: 1, id: 'stop-1', token, method: 'shutdown' })
  assert.equal(response.ok, true)
  assert.deepEqual(response.result, { stopping: true })
  await hooked
  assert.deepEqual(calls, ['shutdown', 'close'])
  const second = new ControlServer({ host: '127.0.0.1', port: 0, token, lifecycle })
  await second.listen()
  test.after(() => second.close())
  const withoutHook = await request(second.port, { version: 1, id: 'stop-2', token, method: 'shutdown' })
  assert.equal(withoutHook.ok, true)
  assert.deepEqual(withoutHook.result, { stopping: true })
})

test('unauthorized shutdown never runs the shutdown hook', async () => {
  const token = 'a'.repeat(43)
  let hookCalls = 0
  const server = new ControlServer({
    host: '127.0.0.1',
    port: 0,
    token,
    lifecycle: {},
    onShutdown: () => { hookCalls++ }
  })
  await server.listen()
  test.after(() => server.close())
  const response = await request(server.port, { version: 1, id: 'stop-3', token: 'wrong', method: 'shutdown' })
  assert.equal(response.error.code, 'unauthorized')
  await new Promise(resolve => setTimeout(resolve, 50))
  assert.equal(hookCalls, 0)
})
