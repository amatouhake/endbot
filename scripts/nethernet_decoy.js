#!/usr/bin/env node
// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

// Test-only decoy: a second NetherNet host answering LAN discovery on this
// machine (like a Minecraft client with a world open to LAN next to BDS).
// The runtime must connect to the configured BDS, never to this decoy.
// Usage: node nethernet_decoy.js <runtime-dir>   (resolves bedrock-protocol there)

const path = require('node:path')

const runtimeDir = path.resolve(process.argv[2] ?? '.')
const { createServer } = require(require.resolve('bedrock-protocol', { paths: [runtimeDir] }))

const server = createServer({
  host: '0.0.0.0',
  port: Number(process.env.DECOY_PORT ?? 19199),
  transport: 'nethernet',
  motd: { motd: 'Endbot E2E Decoy', levelName: 'decoy world' }
})
server.on('connect', client => {
  console.log('decoy: a client connected (the runtime picked the wrong server)')
  client.disconnect('decoy')
})
console.log('decoy: advertising "Endbot E2E Decoy" (decoy world)')
process.on('SIGTERM', () => { server.close(); process.exit(0) })
