// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import crypto from 'node:crypto'
import fs from 'node:fs'

import { loadOrCreateIdentityId, loadOrCreateOwnerKeyPair } from '../src/local-identity.js'

const [kind, filename, pauseMarker] = process.argv.slice(2)

if (process.env.ENDBOT_TEST_PAUSE_BEFORE_PUBLISH === '1') {
  fs.linkSync = () => {
    fs.writeFileSync(pauseMarker, 'candidate complete\n')
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0)
  }
}

function fingerprint (publicKeyDerBase64) {
  return crypto.createHash('sha256').update(Buffer.from(publicKeyDerBase64, 'base64')).digest('hex')
}

process.once('message', () => {
  try {
    if (kind === 'owner-key') {
      const keys = loadOrCreateOwnerKeyPair(filename)
      process.send({ ok: true, value: fingerprint(keys.publicKeyDerBase64), created: keys.created })
    } else if (kind === 'identity-id') {
      process.send({ ok: true, value: loadOrCreateIdentityId(filename) })
    } else {
      throw new Error(`Unsupported worker kind: ${kind}`)
    }
  } catch (error) {
    process.send({ ok: false, error: error.stack })
  }
})

process.send({ ready: true })
