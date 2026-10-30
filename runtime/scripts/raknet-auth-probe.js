#!/usr/bin/env node
// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

// Negative-login probe for scripts/raknet_spike.py. No token or key is logged.
import { randomUUID } from 'node:crypto'
import path from 'node:path'
import protocol from 'bedrock-protocol'

import { loadConfig } from '../src/config.js'
import { attachDirectSignalling, DIRECT_SERVER_NETWORK_ID, directSignallingBase } from '../src/direct-signalling.js'
import { discoverServer } from '../src/discovery.js'
import { createLocalBotAuth } from '../src/local-identity.js'

const [filename, variant] = process.argv.slice(2)
if (!filename || !['wrong-key', 'wrong-issuer', 'wrong-audience', 'auth-disabled'].includes(variant)) {
  throw new Error('Usage: raknet-auth-probe.js <runtime-config> <wrong-key|wrong-issuer|wrong-audience|auth-disabled>')
}
const config = loadConfig(filename)
const direct = config.transport === 'nethernet' && config.nethernetSignalling === 'direct'
if (config.transport === 'nethernet' && !direct && config.serverName === undefined) {
  throw new Error('The NetherNet LAN probe requires serverName and levelName')
}
// NetherNet dials only the expected BDS, as the runtime does (src/protocol-session.js).
const target = config.transport === 'nethernet' && !direct
  ? await discoverServer({
    host: config.serverHost,
    serverName: config.serverName,
    levelName: config.levelName,
    timeoutMs: config.discoveryTimeoutMs
  })
  : undefined
const name = 'RakNetNegative'
const identityId = randomUUID()
const auth = createLocalBotAuth({
  username: name,
  identityId,
  privateKeyPath: variant === 'wrong-key' ? path.join(path.dirname(filename), 'wrong-owner-private.pem') : config.ownerPrivateKeyPath,
  publicKeyPath: variant === 'wrong-key' ? path.join(path.dirname(filename), 'wrong-owner-public.pem') : config.ownerPublicKeyPath,
  issuer: variant === 'wrong-issuer' ? `${config.localAuthIssuer}/wrong` : config.localAuthIssuer,
  audience: variant === 'wrong-audience' ? `${config.localAuthAudience}/wrong` : config.localAuthAudience
})
const result = { variant, loginSent: false, kicked: false, spawned: false, ok: false }
const client = protocol.createClient({
  host: config.serverHost,
  port: config.serverPort,
  version: config.gameVersion,
  transport: config.transport,
  ...(config.transport === 'nethernet'
    ? { nethernet: { signalling: 'lan', networkId: direct ? DIRECT_SERVER_NETWORK_ID : target.networkId } }
    : { raknetBackend: 'raknet-native', followPort: false }),
  skipPing: true,
  offline: false,
  username: name,
  authflow: auth.authflow,
  skinData: { SelfSignedId: identityId },
  connectTimeout: 10000
})
if (direct) {
  // createClient has already allowed the connection; signal over HTTP before the offer.
  attachDirectSignalling(client.connection.nethernet, {
    base: directSignallingBase(config.serverHost, config.serverPort),
    timeoutMs: 10000
  })
}
let settled = false
let finish
const closed = new Promise(resolve => { finish = resolve })
const timer = setTimeout(() => {
  result.error = 'Timed out without a confirmed server rejection'
  complete()
}, 15000)
function complete () {
  if (settled) return
  settled = true
  clearTimeout(timer)
  result.ok = result.loginSent && result.kicked && !result.spawned
  client.close('negative probe finished')
  finish()
}
client.on('loggingIn', () => { result.loginSent = true })
client.on('kick', () => { result.kicked = true })
client.on('spawn', () => { result.spawned = true; complete() })
client.on('error', error => { result.error = error.message; complete() })
client.on('close', complete)
await closed
process.stdout.write(`${JSON.stringify(result)}\n`)
process.exitCode = result.ok ? 0 : 1
