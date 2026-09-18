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

function atomicPrivateWrite (filename, contents) {
  fs.mkdirSync(path.dirname(filename), { recursive: true, mode: 0o700 })
  const temporary = `${filename}.${process.pid}.${crypto.randomBytes(6).toString('hex')}.tmp`
  const descriptor = fs.openSync(temporary, 'wx', 0o600)
  try {
    fs.writeFileSync(descriptor, contents)
  } finally {
    fs.closeSync(descriptor)
  }
  fs.renameSync(temporary, filename)
  fs.chmodSync(filename, 0o600)
}

export function loadOrCreateOwnerKeyPair (privateKeyPath) {
  let privateKey
  let created = false
  if (fs.existsSync(privateKeyPath)) {
    const metadata = fs.lstatSync(privateKeyPath)
    if (!metadata.isFile() || metadata.isSymbolicLink()) {
      throw new Error('Local Endbot private key path must be a regular file, not a symlink')
    }
    fs.chmodSync(privateKeyPath, 0o600)
    privateKey = crypto.createPrivateKey(fs.readFileSync(privateKeyPath))
  } else {
    privateKey = crypto.generateKeyPairSync('ec', { namedCurve: 'secp384r1' }).privateKey
    atomicPrivateWrite(privateKeyPath, privateKey.export({ type: 'pkcs8', format: 'pem' }))
    created = true
  }
  if (privateKey.asymmetricKeyType !== 'ec' || privateKey.asymmetricKeyDetails?.namedCurve !== 'secp384r1') {
    throw new Error('Local Endbot key must be an EC P-384 private key')
  }
  const publicKey = crypto.createPublicKey(privateKey)
  return {
    privateKey,
    publicKey,
    publicKeyPem: publicKey.export({ type: 'spki', format: 'pem' }),
    publicKeyDerBase64: publicKey.export({ type: 'spki', format: 'der' }).toString('base64'),
    created
  }
}

export function writePublicKey (publicKeyPath, publicKeyPem) {
  fs.mkdirSync(path.dirname(publicKeyPath), { recursive: true })
  fs.writeFileSync(publicKeyPath, publicKeyPem, { mode: 0o644 })
}

export function loadOrCreateIdentityId (identityPath) {
  if (fs.existsSync(identityPath)) {
    const metadata = fs.lstatSync(identityPath)
    if (!metadata.isFile() || metadata.isSymbolicLink()) {
      throw new Error('Local Endbot identity path must be a regular file, not a symlink')
    }
    const identityId = fs.readFileSync(identityPath, 'utf8').trim()
    assertUuid(identityId, 'identityId')
    return identityId
  }
  const identityId = crypto.randomUUID()
  atomicPrivateWrite(identityPath, `${identityId}\n`)
  return identityId
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
