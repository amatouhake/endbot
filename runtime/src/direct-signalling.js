// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

// NetherNet direct-connect signalling: the HTTP exchange a client uses to join
// a server by address. The client POSTs its SDP offer, with every ICE
// candidate already in it, to <base>/v1/join/<client network id> on the game
// port and gets the SDP answer back in the same response; there is no trickle
// ICE and no UDP 7551 discovery. Upstream bedrock-protocol only signals over
// LAN discovery or the online services, so Endbot replaces the NetherNet
// client's signal handler with this one (docs/OPERATIONS.md section 5).

// Upstream requires a server network id and matches answers against it; over
// HTTP the server is named by its URL, so any fixed nonzero id serves.
export const DIRECT_SERVER_NETWORK_ID = 1n
export const DEFAULT_SIGNALLING_TIMEOUT_MS = 10_000
const MAX_SDP_BYTES = 1 << 20

const CONNECT_REQUEST = 'CONNECTREQUEST'
const CONNECT_RESPONSE = 'CONNECTRESPONSE'

export function directSignallingBase (host, port) {
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535) {
    throw new Error('NetherNet direct signalling needs a server port from 1 to 65535')
  }
  return `http://${host.includes(':') ? `[${host}]` : host}:${port}`
}

export async function postOffer ({ base, networkId, offer, timeoutMs = DEFAULT_SIGNALLING_TIMEOUT_MS, fetch = globalThis.fetch }) {
  if (!/^a=candidate:/m.test(offer)) throw new Error('NetherNet offer has no ICE candidates for direct signalling')
  const url = `${base}/v1/join/${networkId}`
  let response
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp', 'User-Agent': 'libhttpclient/1.0.0.0' },
      body: offer,
      signal: AbortSignal.timeout(timeoutMs)
    })
  } catch (error) {
    throw new Error(`NetherNet direct signalling to ${url} failed: ${error.message}`, { cause: error })
  }
  const answer = await response.text()
  if (!response.ok) {
    throw new Error(`NetherNet direct signalling to ${url} returned ${response.status}: ${answer.slice(0, 200)}`)
  }
  if (Buffer.byteLength(answer) > MAX_SDP_BYTES) throw new Error(`NetherNet answer from ${url} is too large`)
  // The server reports a failed negotiation as a bare NetherNet error code.
  if (/^\d+$/.test(answer.trim())) throw new Error(`NetherNet negotiation with ${url} failed with error code ${answer.trim()}`)
  if (!answer) throw new Error(`NetherNet direct signalling to ${url} returned no SDP answer`)
  return answer
}

// Route a bedrock-protocol NetherNet client's signals over HTTP. Call this on
// `connect_allowed`, before authentication finishes and the offer is created.
export function attachDirectSignalling (nethernet, { base, timeoutMs, fetch }) {
  nethernet.signalHandler = signal => {
    // Candidates are already in the offer and the endpoint has no channel for
    // client-side errors; the negotiation timeout covers a lost answer.
    if (signal.type !== CONNECT_REQUEST) return
    postOffer({ base, networkId: nethernet.networkId, offer: signal.data, timeoutMs, fetch }).then(answer => {
      nethernet.handleSignal({
        type: CONNECT_RESPONSE,
        connectionId: signal.connectionId,
        data: answer,
        networkId: signal.networkId
      })
    }, error => nethernet.reportError(error))
  }
}
