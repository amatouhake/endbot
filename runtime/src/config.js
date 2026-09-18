// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import fs from 'node:fs'
import path from 'node:path'

export function loadConfig (filename) {
  const root = path.dirname(path.resolve(filename))
  const value = JSON.parse(fs.readFileSync(filename, 'utf8'))
  const resolve = name => path.resolve(root, value[name])
  return {
    dataDirectory: resolve('dataDirectory'),
    controlTokenPath: resolve('controlTokenPath'),
    ownerPrivateKeyPath: resolve('ownerPrivateKeyPath'),
    ownerPublicKeyPath: value.ownerPublicKeyPath ? resolve('ownerPublicKeyPath') : undefined,
    serverIdentityPinPath: resolve('serverIdentityPinPath'),
    controlHost: value.controlHost ?? '127.0.0.1',
    controlPort: value.controlPort ?? 19142,
    serverHost: value.serverHost ?? '127.0.0.1',
    serverPort: value.serverPort ?? 19132,
    gameVersion: value.gameVersion ?? '1.26.50',
    protocol: value.protocol ?? 2193,
    connectTimeoutMs: value.connectTimeoutMs ?? 15_000,
    localAuthIssuer: value.localAuthIssuer ?? 'ownerbot://local',
    localAuthAudience: value.localAuthAudience ?? 'endstone://local-ownerbot',
    reconnect: value.reconnect ?? {}
  }
}
