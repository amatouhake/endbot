// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import fs from 'node:fs'
import path from 'node:path'

export const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1'])

export function loadConfig (filename) {
  const root = path.dirname(path.resolve(filename))
  const value = JSON.parse(fs.readFileSync(filename, 'utf8'))
  const resolve = name => path.resolve(root, value[name])
  const serverHost = value.serverHost ?? '127.0.0.1'
  // See docs/SECURITY.md: the BDS connection is trusted because it is
  // same-host loopback, not because the server identity is pinned.
  if (!LOOPBACK_HOSTS.has(serverHost)) throw new Error('Endbot serverHost must be a loopback address')
  return {
    dataDirectory: resolve('dataDirectory'),
    controlTokenPath: resolve('controlTokenPath'),
    ownerPrivateKeyPath: resolve('ownerPrivateKeyPath'),
    ownerPublicKeyPath: value.ownerPublicKeyPath ? resolve('ownerPublicKeyPath') : undefined,
    controlHost: value.controlHost ?? '127.0.0.1',
    controlPort: value.controlPort ?? 19142,
    controlRequestTimeoutMs: value.controlRequestTimeoutMs ?? 8_000,
    serverHost,
    serverPort: value.serverPort ?? 19132,
    gameVersion: value.gameVersion ?? '1.26.51',
    protocol: value.protocol ?? 2193,
    connectTimeoutMs: value.connectTimeoutMs ?? 15_000,
    localAuthIssuer: value.localAuthIssuer ?? 'endbot://local-bot',
    localAuthAudience: value.localAuthAudience ?? 'endstone://local-bot',
    reconnect: value.reconnect ?? {}
  }
}
