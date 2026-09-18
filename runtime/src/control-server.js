// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import crypto from 'node:crypto'
import net from 'node:net'

const MAX_REQUEST_BYTES = 64 * 1024
const LOOPBACK = new Set(['127.0.0.1', '::1', 'localhost'])

function tokensEqual (left, right) {
  if (typeof left !== 'string' || typeof right !== 'string') return false
  const a = Buffer.from(left)
  const b = Buffer.from(right)
  return a.length === b.length && crypto.timingSafeEqual(a, b)
}

export class ControlServer {
  constructor ({ host = '127.0.0.1', port = 19142, token, lifecycle, requestTimeoutMs = 8_000 }) {
    if (!LOOPBACK.has(host)) throw new Error('Endbot control server must bind to loopback')
    if (typeof token !== 'string' || token.length < 32) throw new Error('Endbot control token is invalid')
    this.host = host
    this.port = port
    this.token = token
    this.lifecycle = lifecycle
    this.requestTimeoutMs = requestTimeoutMs
    this.sockets = new Set()
  }

  listen () {
    this.server = net.createServer(socket => this.#connection(socket))
    return new Promise((resolve, reject) => {
      this.server.once('error', reject)
      this.server.listen(this.port, this.host, () => {
        this.server.off('error', reject)
        const address = this.server.address()
        this.port = typeof address === 'object' ? address.port : this.port
        resolve(address)
      })
    })
  }

  close () {
    if (!this.server) return Promise.resolve()
    for (const socket of this.sockets) socket.destroy()
    return new Promise((resolve, reject) => this.server.close(error => error ? reject(error) : resolve()))
  }

  #connection (socket) {
    this.sockets.add(socket)
    socket.once('close', () => this.sockets.delete(socket))
    socket.setTimeout(this.requestTimeoutMs, () => socket.destroy())
    let body = ''
    socket.setEncoding('utf8')
    socket.on('data', chunk => {
      body += chunk
      if (Buffer.byteLength(body) > MAX_REQUEST_BYTES) socket.destroy()
      if (!body.includes('\n')) return
      socket.removeAllListeners('data')
      void this.#respond(socket, body.slice(0, body.indexOf('\n')))
    })
  }

  async #respond (socket, line) {
    let request
    try {
      request = JSON.parse(line)
      if (!request || request.version !== 1 || typeof request.id !== 'string') throw new Error('Invalid control request')
      if (!tokensEqual(request.token, this.token)) {
        socket.end(`${JSON.stringify({ version: 1, id: request.id, ok: false, error: { code: 'unauthorized', message: 'Unauthorized' } })}\n`)
        return
      }
      const result = await this.#dispatch(request.method, request.params ?? {})
      socket.end(`${JSON.stringify({ version: 1, id: request.id, ok: true, result })}\n`)
    } catch (error) {
      socket.end(`${JSON.stringify({
        version: 1,
        id: typeof request?.id === 'string' ? request.id : '',
        ok: false,
        error: { code: error.code ?? 'invalid_request', message: String(error.message ?? error) }
      })}\n`)
    }
  }

  #dispatch (method, params) {
    switch (method) {
      case 'ping': return { runtime: 'endbot', protocolVersion: 1 }
      case 'list': return this.lifecycle.list()
      case 'status': return this.lifecycle.status(params.name)
      case 'spawn': return this.lifecycle.spawn(params.name)
      case 'resume': return this.lifecycle.resume(params.name)
      case 'reconnect': return this.lifecycle.reconnectBot(params.name)
      case 'despawn': return this.lifecycle.despawn(params.name)
      case 'forget': return this.lifecycle.forget(params.name)
      case 'rename': return this.lifecycle.rename(params.name, params.newName)
      case 'move': return this.lifecycle.move(params.name, params.direction)
      case 'look': return this.lifecycle.look(params.name, params.yaw, params.pitch)
      case 'action': return this.lifecycle.action(params.name, params.action, params.mode, params.intervalTicks)
      case 'flag': return this.lifecycle.flag(params.name, params.flag, params.value)
      case 'stop': return this.lifecycle.stop(params.name)
      default: throw Object.assign(new Error(`Unknown control method: ${method}`), { code: 'unknown_method' })
    }
  }
}
