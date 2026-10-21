// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import crypto from 'node:crypto'
import net from 'node:net'

const MAX_REQUEST_BYTES = 64 * 1024
const LOOPBACK = new Set(['127.0.0.1', '::1', 'localhost'])
const GROUP_INPUT_METHODS = new Set(['move', 'look', 'action', 'flag', 'stop', 'hotbar', 'drop'])

function tokensEqual (left, right) {
  if (typeof left !== 'string' || typeof right !== 'string') return false
  const a = Buffer.from(left)
  const b = Buffer.from(right)
  return a.length === b.length && crypto.timingSafeEqual(a, b)
}

export class ControlServer {
  constructor ({ host = '127.0.0.1', port = 19142, token, lifecycle, requestTimeoutMs = 8_000, onShutdown } = {}) {
    if (!LOOPBACK.has(host)) throw new Error('Endbot control server must bind to loopback')
    if (typeof token !== 'string' || token.length < 32) throw new Error('Endbot control token is invalid')
    this.host = host
    this.port = port
    this.token = token
    this.lifecycle = lifecycle
    this.requestTimeoutMs = requestTimeoutMs
    this.onShutdown = onShutdown
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
      // The shutdown hook runs only after the response has left the socket,
      // so the caller always learns that a graceful stop was accepted.
      if (request.method === 'shutdown') socket.once('close', () => this.#invokeShutdown())
    } catch (error) {
      socket.end(`${JSON.stringify({
        version: 1,
        id: typeof request?.id === 'string' ? request.id : '',
        ok: false,
        error: { code: error.code ?? 'invalid_request', message: String(error.message ?? error) }
      })}\n`)
    }
  }

  #invokeShutdown () {
    const hook = this.onShutdown
    this.onShutdown = undefined
    if (hook) void Promise.resolve().then(hook).catch(() => {})
  }

  #dispatch (method, params) {
    // Group membership is UUID-based. A stale name must never retarget an
    // operation to a replacement profile, including implicit spawn creation.
    if (params.expectedIdentityId !== undefined) {
      if (typeof params.expectedIdentityId !== 'string' || typeof params.name !== 'string' ||
          this.lifecycle.identityOf(params.name) !== params.expectedIdentityId) {
        throw Object.assign(new Error('Bot identity changed; group operation canceled'), { code: 'identity_changed' })
      }
    }
    switch (method) {
      case 'ping': return { runtime: 'endbot', protocolVersion: 1 }
      case 'capabilities': return { identityPinnedRequests: true, groupInputBatch: true }
      case 'groupInputBatch': return this.#groupInputBatch(params)
      case 'shutdown': return { stopping: true }
      case 'list': return this.lifecycle.list()
      case 'status': return this.lifecycle.status(params.name)
      case 'spawn': return this.lifecycle.spawn(params.name)
      case 'resume': return this.lifecycle.resume(params.name)
      case 'reconnect': return this.lifecycle.reconnectBot(params.name)
      case 'despawn': return this.lifecycle.despawn(params.name)
      case 'forget': return this.lifecycle.forget(params.name)
      case 'rename': return this.lifecycle.rename(params.name, params.newName)
      case 'move': return this.lifecycle.move(params.name, params.direction, params.durationTicks)
      case 'look': return this.lifecycle.look(params.name, params.yaw, params.pitch)
      case 'action': return this.lifecycle.action(params.name, params.action, params.mode, params.intervalTicks, params.durationTicks,
        params.holdTicks)
      case 'flag': return this.lifecycle.flag(params.name, params.flag, params.value)
      case 'hotbar': return this.lifecycle.hotbar(params.name, params.slot)
      case 'radius': return this.lifecycle.radius(params.name, params.value)
      case 'interact': return this.lifecycle.interact(params.name, params)
      case 'mine': return this.lifecycle.mine(params.name, params)
      case 'drop': return this.lifecycle.drop(params.name, params.stack)
      case 'stop': return this.lifecycle.stop(params.name)
      default: throw Object.assign(new Error(`Unknown control method: ${method}`), { code: 'unknown_method' })
    }
  }

  #groupInputBatch ({ requests } = {}) {
    // Only synchronous input operations are allowed. No await or timer can
    // interleave another command between members of this dispatch turn.
    // Validate the envelope before any effect; individual Bot errors remain partial.
    if (!Array.isArray(requests) || requests.length === 0 || requests.some(entry =>
      !entry || !GROUP_INPUT_METHODS.has(entry.method) ||
      typeof entry.params?.name !== 'string' ||
      typeof entry.params?.expectedIdentityId !== 'string' || !entry.params.expectedIdentityId)) {
      throw new Error('Invalid group input batch')
    }
    return {
      results: requests.map(({ method, params }) => {
        try {
          const result = this.#dispatch(method, params)
          // Batch replies only need the fields used by immediate-input replies;
          // avoid multiplying full inventories/mining status by the group size.
          return { ok: true, result: { name: result.name, connectionState: result.connectionState,
            ...(method === 'hotbar' ? { selectedHotbarSlot: result.selectedHotbarSlot } : {}) } }
        } catch (error) {
          return { ok: false, error: { code: error.code ?? 'invalid_request', message: String(error.message ?? error) } }
        }
      })
    }
  }
}
