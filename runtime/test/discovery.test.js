// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test from 'node:test'

import { discoverServer, ServerDiscoveryError } from '../src/discovery.js'

function fakeNethernet (replies) {
  const created = []
  class Client extends EventEmitter {
    constructor (networkId, host) {
      super()
      this.host = host
      this.closed = false
      created.push(this)
    }

    ping () {
      for (const reply of replies) setImmediate(() => this.emit('pong', reply))
    }

    close () { this.closed = true }
  }
  return { module: { Client }, created }
}

const protocol = {
  NethernetServerAdvertisement: {
    fromBuffer (buffer) {
      const text = buffer.toString('utf8')
      if (text === 'garbage') throw new Error('unreadable')
      const [motd, levelName] = text.split('|')
      return { motd, levelName }
    }
  }
}

const advertise = (id, motd, levelName) => ({ sender_id: id, data: Buffer.from(`${motd}|${levelName}`).toString('hex') })

test('the configured BDS is chosen even when another LAN host answers first', async () => {
  const { module, created } = fakeNethernet([
    { sender_id: 1n, data: Buffer.from('garbage').toString('hex') },
    advertise(2282111279482269435n, 'amatouhake', 'LaminaSort Test'),
    advertise(42n, 'Endstone Server', 'Bedrock level')
  ])
  const result = await discoverServer({
    host: '127.0.0.1', serverName: 'Endstone Server', levelName: 'Bedrock level', nethernet: module, protocol
  })
  assert.equal(result.networkId, 42n)
  assert.equal(created[0].host, '127.0.0.1')
  assert.equal(created[0].closed, true)
})

test('no matching advertisement fails with the names that were seen', async () => {
  const { module, created } = fakeNethernet([advertise(7n, 'amatouhake', 'LaminaSort Test')])
  await assert.rejects(
    discoverServer({
      host: '127.0.0.1', serverName: 'Endstone Server', levelName: 'Bedrock level', timeoutMs: 50, nethernet: module, protocol
    }),
    error => error instanceof ServerDiscoveryError &&
      error.code === 'server_not_found' &&
      /Endstone Server/.test(error.message) &&
      /LaminaSort Test/.test(error.message)
  )
  assert.equal(created[0].closed, true)
})

test('level-name must match as well as server-name', async () => {
  const { module } = fakeNethernet([advertise(9n, 'Endstone Server', 'Other World')])
  await assert.rejects(
    discoverServer({
      host: '127.0.0.1', serverName: 'Endstone Server', levelName: 'Bedrock level', timeoutMs: 50, nethernet: module, protocol
    }),
    ServerDiscoveryError
  )
})
