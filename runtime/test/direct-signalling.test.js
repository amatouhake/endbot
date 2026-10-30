// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import http from 'node:http'
import { createRequire } from 'node:module'
import test from 'node:test'

import { attachDirectSignalling, directSignallingBase, postOffer } from '../src/direct-signalling.js'

const require = createRequire(import.meta.url)
const OFFER = 'v=0\r\na=candidate:1 1 udp 2116026367 127.0.0.1 50000 typ host\r\n'

function fakeFetch (status, body, calls = []) {
  return async (url, init) => {
    calls.push({ url, init })
    return new Response(body, { status })
  }
}

test('the signalling base names the configured loopback port', () => {
  assert.equal(directSignallingBase('127.0.0.1', 19132), 'http://127.0.0.1:19132')
  assert.equal(directSignallingBase('::1', 19134), 'http://[::1]:19134')
  assert.throws(() => directSignallingBase('127.0.0.1', 0), /server port/)
})

test('an offer is posted to the join endpoint and the SDP answer returned', async () => {
  const calls = []
  const answer = await postOffer({ base: 'http://127.0.0.1:19134', networkId: 42n, offer: OFFER, fetch: fakeFetch(200, 'v=0 answer', calls) })
  assert.equal(answer, 'v=0 answer')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, 'http://127.0.0.1:19134/v1/join/42')
  assert.equal(calls[0].init.method, 'POST')
  assert.equal(calls[0].init.headers['Content-Type'], 'application/sdp')
  assert.equal(calls[0].init.body, OFFER)
})

test('direct signalling refuses offers it cannot complete and reports server failures', async () => {
  const base = 'http://127.0.0.1:19134'
  await assert.rejects(postOffer({ base, networkId: 1n, offer: 'v=0\r\n', fetch: fakeFetch(200, 'x') }), /no ICE candidates/)
  await assert.rejects(postOffer({ base, networkId: 1n, offer: OFFER, fetch: fakeFetch(503, 'Service unavailable') }), /returned 503/)
  await assert.rejects(postOffer({ base, networkId: 1n, offer: OFFER, fetch: fakeFetch(200, '14') }), /error code 14/)
  await assert.rejects(postOffer({ base, networkId: 1n, offer: OFFER, fetch: fakeFetch(200, '') }), /no SDP answer/)
  await assert.rejects(postOffer({
    base,
    networkId: 1n,
    offer: OFFER,
    fetch: async () => { throw new Error('ECONNREFUSED') }
  }), /http:\/\/127\.0\.0\.1:19134\/v1\/join\/1 failed: ECONNREFUSED/)
})

test('only the offer is signalled and its answer is handed back to the NetherNet client', async () => {
  const handled = []
  const errors = []
  const calls = []
  const nethernet = { networkId: 9n, handleSignal: signal => handled.push(signal), reportError: error => errors.push(error) }
  attachDirectSignalling(nethernet, { base: 'http://127.0.0.1:19134', fetch: fakeFetch(200, 'answer', calls) })
  nethernet.signalHandler({ type: 'CANDIDATEADD', connectionId: 5n, data: 'candidate', networkId: 1n })
  nethernet.signalHandler({ type: 'CONNECTREQUEST', connectionId: 5n, data: OFFER, networkId: 1n })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, 'http://127.0.0.1:19134/v1/join/9')
  assert.deepEqual(handled, [{ type: 'CONNECTRESPONSE', connectionId: 5n, data: 'answer', networkId: 1n }])
  assert.deepEqual(errors, [])

  attachDirectSignalling(nethernet, { base: 'http://127.0.0.1:19134', fetch: fakeFetch(500, 'boom') })
  nethernet.signalHandler({ type: 'CONNECTREQUEST', connectionId: 6n, data: OFFER, networkId: 1n })
  await new Promise(resolve => setImmediate(resolve))
  assert.match(errors[0].message, /returned 500/)
})

// A local stand-in for the BDS endpoint: the upstream NetherNet server answers
// offers posted over loopback HTTP. It shows the client side of the exchange
// works without UDP 7551; it is not evidence that BDS accepts it.
test('a NetherNet client connects through HTTP signalling without LAN discovery', async t => {
  const { Client, Server } = require('nethernet')
  const server = new Server()
  const http_ = http.createServer((request, response) => {
    const match = /^\/v1\/join\/(\d+)$/.exec(request.url)
    if (request.method !== 'POST' || !match) return response.writeHead(404).end()
    let body = ''
    request.on('data', chunk => { body += chunk })
    request.on('end', () => {
      server.handleOffer({ type: 'CONNECTREQUEST', connectionId: 77n, data: body, networkId: BigInt(match[1]) }, signal => {
        if (signal.type === 'CONNECTRESPONSE') response.writeHead(200, { 'Content-Type': 'application/sdp' }).end(signal.data)
        if (signal.type === 'CONNECTERROR') response.writeHead(500).end(signal.data)
      })
    })
  })
  await new Promise(resolve => http_.listen(0, '127.0.0.1', resolve))
  const client = new Client(1n, '127.0.0.1', { connectionId: 77n })
  t.after(() => {
    client.close('test cleanup')
    server.close('test cleanup')
    http_.close()
  })
  attachDirectSignalling(client, { base: directSignallingBase('127.0.0.1', http_.address().port) })

  const opened = new Promise((resolve, reject) => {
    server.once('openConnection', resolve)
    client.once('error', reject)
  })
  const received = new Promise(resolve => server.once('encapsulated', resolve))
  const connected = new Promise(resolve => client.once('connected', resolve))
  client.connect()
  await Promise.all([opened, connected])
  client.send(Buffer.from([1, 2, 3]))
  assert.deepEqual([...await received], [1, 2, 3])
})
