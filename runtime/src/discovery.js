// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)

export const DEFAULT_DISCOVERY_TIMEOUT_MS = 8_000

export class ServerDiscoveryError extends Error {
  constructor (expected, seen) {
    const found = seen.length ? seen.join('; ') : 'none'
    super(
      `No NetherNet server on this host advertises server-name ${JSON.stringify(expected.serverName)} with ` +
      `level-name ${JSON.stringify(expected.levelName)}; advertisements seen: ${found}`
    )
    this.code = 'server_not_found'
  }
}

function describe (advertisement) {
  return `${JSON.stringify(advertisement.motd)} (${JSON.stringify(advertisement.levelName)})`
}

// Several NetherNet hosts can answer LAN discovery on one machine (for
// example a Minecraft client with a world open to LAN next to BDS), and the
// advertisement carries no port. Upstream bedrock-protocol connects to the
// first reply, so pick the configured BDS by the names it advertises, which
// are its server.properties server-name (motd) and level-name.
export async function discoverServer ({
  host,
  serverName,
  levelName,
  timeoutMs = DEFAULT_DISCOVERY_TIMEOUT_MS,
  nethernet = require('nethernet'),
  protocol = require('bedrock-protocol')
}) {
  const client = new nethernet.Client(0n, host)
  const seen = new Map()
  try {
    return await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new ServerDiscoveryError({ serverName, levelName }, [...seen.values()]))
      }, timeoutMs)
      client.on('pong', packet => {
        let advertisement
        try {
          advertisement = protocol.NethernetServerAdvertisement.fromBuffer(Buffer.from(packet.data, 'hex'))
        } catch {
          return
        }
        const networkId = BigInt(packet.sender_id)
        seen.set(networkId.toString(), describe(advertisement))
        if (advertisement.motd === serverName && advertisement.levelName === levelName) {
          clearTimeout(timer)
          resolve({ networkId, advertisement })
        }
      })
      client.ping()
    })
  } finally {
    client.close()
  }
}
