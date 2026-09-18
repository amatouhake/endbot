// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

export const DEFAULT_ISSUER = 'ownerbot://local'
export const DEFAULT_AUDIENCE = 'endstone://local-ownerbot'

function base64Url (value) {
  return Buffer.from(value).toString('base64url')
}

function assertName (name) {
  const length = Buffer.byteLength(name, 'utf8')
  if (length < 1 || length > 64) throw new TypeError('Bot name must contain between 1 and 64 UTF-8 bytes')
}

function assertUuid (value, field) {
  const pattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
  if (!pattern.test(value)) throw new TypeError(`${field} must be an RFC 4122 UUID`)
}

const publicationWait = new Int32Array(new SharedArrayBuffer(4))
const publicationRetryLimit = 200
const publicationRetryMilliseconds = 5

function waitForPublication () {
  Atomics.wait(publicationWait, 0, 0, publicationRetryMilliseconds)
}

function regularFileExists (filename, description) {
  let metadata
  try {
    metadata = fs.lstatSync(filename)
  } catch (error) {
    if (error.code === 'ENOENT') return false
    throw error
  }
  if (!metadata.isFile() || metadata.isSymbolicLink()) {
    throw new Error(`${description} path must be a regular file, not a symlink`)
  }
  return true
}

function applyPrivateMode (filename) {
  if (process.platform !== 'win32') fs.chmodSync(filename, 0o600)
}

function loadExistingArtifact (filename, description, loadAndValidate) {
  if (!regularFileExists(filename, description)) return { exists: false }
  applyPrivateMode(filename)
  return { exists: true, value: loadAndValidate(filename) }
}

function loadPublishedWinner (filename, description, loadAndValidate) {
  let lastError
  // COPYFILE_EXCL reserves the destination before its contents are necessarily
  // visible in full. A loser may therefore need to wait for the winner's copy.
  for (let attempt = 0; attempt < publicationRetryLimit; attempt += 1) {
    try {
      const loaded = loadExistingArtifact(filename, description, loadAndValidate)
      if (loaded.exists) return loaded.value
    } catch (error) {
      lastError = error
    }
    waitForPublication()
  }
  throw new Error(`Concurrent ${description} publication did not produce a valid artifact`, { cause: lastError })
}

function loadOrCreatePersistentArtifact (filename, create, loadAndValidate, description) {
  const existing = loadExistingArtifact(filename, description, loadAndValidate)
  if (existing.exists) return { value: existing.value, created: false }

  fs.mkdirSync(path.dirname(filename), { recursive: true, mode: 0o700 })
  const temporary = `${filename}.${process.pid}.${crypto.randomBytes(6).toString('hex')}.tmp`
  try {
    const descriptor = fs.openSync(temporary, 'wx', 0o600)
    try {
      fs.writeFileSync(descriptor, create())
      fs.fsyncSync(descriptor)
    } finally {
      fs.closeSync(descriptor)
    }

    try {
      fs.copyFileSync(temporary, filename, fs.constants.COPYFILE_EXCL)
    } catch (error) {
      if (error.code !== 'EEXIST') throw error
      return {
        value: loadPublishedWinner(filename, description, loadAndValidate),
        created: false
      }
    }
    applyPrivateMode(filename)
    return {
      value: loadExistingArtifact(filename, description, loadAndValidate).value,
      created: true
    }
  } finally {
    fs.rmSync(temporary, { force: true })
  }
}

export function loadOrCreateOwnerKeyPair (privateKeyPath) {
  const result = loadOrCreatePersistentArtifact(
    privateKeyPath,
    () => crypto.generateKeyPairSync('ec', { namedCurve: 'secp384r1' }).privateKey
      .export({ type: 'pkcs8', format: 'pem' }),
    (filename) => {
      const privateKey = crypto.createPrivateKey(fs.readFileSync(filename))
      if (privateKey.asymmetricKeyType !== 'ec' || privateKey.asymmetricKeyDetails?.namedCurve !== 'secp384r1') {
        throw new Error('Local Endbot key must be an EC P-384 private key')
      }
      return privateKey
    },
    'Local Endbot private key'
  )
  const privateKey = result.value
  const publicKey = crypto.createPublicKey(privateKey)
  return {
    privateKey,
    publicKey,
    publicKeyPem: publicKey.export({ type: 'spki', format: 'pem' }),
    publicKeyDerBase64: publicKey.export({ type: 'spki', format: 'der' }).toString('base64'),
    created: result.created
  }
}

export function writePublicKey (publicKeyPath, publicKeyPem) {
  fs.mkdirSync(path.dirname(publicKeyPath), { recursive: true })
  fs.writeFileSync(publicKeyPath, publicKeyPem, { mode: 0o644 })
}

export function loadOrCreateIdentityId (identityPath) {
  return loadOrCreatePersistentArtifact(
    identityPath,
    () => `${crypto.randomUUID()}\n`,
    (filename) => {
      const identityId = fs.readFileSync(filename, 'utf8').trim()
      assertUuid(identityId, 'identityId')
      return identityId
    },
    'Local Endbot identity'
  ).value
}

export function signCompact (payload, privateKey, header = {}) {
  const encodedHeader = base64Url(JSON.stringify({ alg: 'ES384', kid: 'endbot-local', ...header }))
  const encodedPayload = base64Url(JSON.stringify(payload))
  const signingInput = `${encodedHeader}.${encodedPayload}`
  const signature = crypto.sign('sha384', Buffer.from(signingInput), {
    key: privateKey,
    dsaEncoding: 'ieee-p1363'
  })
  return `${signingInput}.${signature.toString('base64url')}`
}

export function tamperPayload (token, changes) {
  const parts = token.split('.')
  if (parts.length !== 3) throw new TypeError('Expected a compact JWS')
  const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'))
  parts[1] = base64Url(JSON.stringify({ ...payload, ...changes }))
  return parts.join('.')
}

export function createLocalIdentityToken ({
  privateKey,
  identityId,
  username,
  clientPublicKey,
  issuer = DEFAULT_ISSUER,
  audience = DEFAULT_AUDIENCE,
  now = Math.floor(Date.now() / 1000),
  lifetimeSeconds = 60,
  tokenId = crypto.randomUUID(),
  variant = 'valid'
}) {
  assertName(username)
  assertUuid(identityId, 'identityId')
  assertUuid(tokenId, 'tokenId')
  if (!Number.isSafeInteger(lifetimeSeconds) || lifetimeSeconds < 1 || lifetimeSeconds > 120) {
    throw new RangeError('Token lifetime must be between 1 and 120 seconds')
  }

  let tokenIssuer = issuer
  let cpk = clientPublicKey
  let issuedAt = now
  let notBefore = now - 1
  let expiresAt = now + lifetimeSeconds
  if (variant === 'wrong-issuer') tokenIssuer = `${issuer}/wrong`
  if (variant === 'expired') {
    issuedAt = now - 300
    notBefore = now - 300
    expiresAt = now - 180
  }
  if (variant === 'wrong-cpk') {
    cpk = crypto.generateKeyPairSync('ec', { namedCurve: 'secp384r1' }).publicKey
      .export({ type: 'spki', format: 'der' }).toString('base64')
  }
  if (!['valid', 'wrong-issuer', 'expired', 'wrong-cpk', 'tampered'].includes(variant)) {
    throw new TypeError(`Unsupported local identity variant: ${variant}`)
  }

  const token = signCompact({
    iss: tokenIssuer,
    aud: audience,
    sub: identityId,
    xname: username,
    cpk,
    jti: tokenId,
    iat: issuedAt,
    nbf: notBefore,
    exp: expiresAt
  }, privateKey)
  return variant === 'tampered' ? tamperPayload(token, { xname: `${username}-tampered` }) : token
}

function createProfileCertificate ({ privateKey, publicKeyDerBase64, identityId, username, clientPublicKey, issuer }) {
  return signCompact({
    iss: issuer,
    extraData: { displayName: username, identity: identityId },
    certificateAuthority: false,
    identityPublicKey: clientPublicKey
  }, privateKey, { x5u: publicKeyDerBase64 })
}

export function createLocalOwnerbotAuth ({
  username,
  privateKeyPath,
  publicKeyPath,
  issuer = DEFAULT_ISSUER,
  audience = DEFAULT_AUDIENCE,
  identityId,
  lifetimeSeconds = 60,
  variant = 'valid'
}) {
  assertName(username)
  assertUuid(identityId, 'identityId')
  const keys = loadOrCreateOwnerKeyPair(privateKeyPath)
  if (publicKeyPath) writePublicKey(publicKeyPath, keys.publicKeyPem)

  const authflow = {
    async getMinecraftBedrockToken (clientPublicKey) {
      const token = createLocalIdentityToken({
        privateKey: keys.privateKey,
        issuer,
        audience,
        identityId,
        username,
        clientPublicKey,
        lifetimeSeconds,
        variant
      })
      const profileCertificate = createProfileCertificate({
        privateKey: keys.privateKey,
        publicKeyDerBase64: keys.publicKeyDerBase64,
        identityId,
        username,
        clientPublicKey,
        issuer
      })
      return { chain: [profileCertificate, profileCertificate], token }
    }
  }
  return {
    authflow,
    identityId,
    issuer,
    audience,
    publicKeyPem: keys.publicKeyPem,
    created: keys.created
  }
}
