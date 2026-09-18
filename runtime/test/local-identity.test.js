// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  createLocalIdentityToken,
  createLocalOwnerbotAuth,
  loadOrCreateIdentityId,
  loadOrCreateOwnerKeyPair,
  tamperPayload
} from '../src/local-identity.js'

function temporaryDirectory (prefix) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), prefix))
  test.after(() => fs.rmSync(directory, { recursive: true }))
  return directory
}

function verifyCompact (token, publicKey) {
  const [header, payload, signature] = token.split('.')
  const valid = crypto.verify('sha384', Buffer.from(`${header}.${payload}`), {
    key: publicKey,
    dsaEncoding: 'ieee-p1363'
  }, Buffer.from(signature, 'base64url'))
  return { valid, header: JSON.parse(Buffer.from(header, 'base64url')), claims: JSON.parse(Buffer.from(payload, 'base64url')) }
}

test('generates and reuses a mode-0600 P-384 owner key', () => {
  const directory = temporaryDirectory('endbot-key-')
  const filename = path.join(directory, 'owner-private.pem')
  const first = loadOrCreateOwnerKeyPair(filename)
  fs.chmodSync(filename, 0o644)
  const second = loadOrCreateOwnerKeyPair(filename)
  assert.equal(first.created, true)
  assert.equal(second.created, false)
  assert.equal(first.publicKeyDerBase64, second.publicKeyDerBase64)
  if (process.platform !== 'win32') assert.equal(fs.statSync(filename).mode & 0o777, 0o600)
  assert.equal(first.privateKey.asymmetricKeyDetails.namedCurve, 'secp384r1')
})

test('refuses a symlink as a private-key path', () => {
  const directory = temporaryDirectory('endbot-key-link-')
  const real = path.join(directory, 'real.pem')
  loadOrCreateOwnerKeyPair(real)
  const link = path.join(directory, 'linked.pem')
  fs.symlinkSync(real, link)
  assert.throws(() => loadOrCreateOwnerKeyPair(link), /regular file, not a symlink/)
})

test('persists an identity UUID independently from the user-visible name', () => {
  const directory = temporaryDirectory('endbot-identity-')
  const filename = path.join(directory, 'bot.uuid')
  const identityId = loadOrCreateIdentityId(filename)
  assert.equal(loadOrCreateIdentityId(filename), identityId)
  assert.match(identityId, /^[0-9a-f-]{36}$/)
})

test('creates a valid ES384 local identity bound to the client key', () => {
  const owner = crypto.generateKeyPairSync('ec', { namedCurve: 'secp384r1' })
  const client = crypto.generateKeyPairSync('ec', { namedCurve: 'secp384r1' })
  const cpk = client.publicKey.export({ type: 'spki', format: 'der' }).toString('base64')
  const identityId = '63572362-0c83-5f0a-8cec-e1b788101798'
  const token = createLocalIdentityToken({
    privateKey: owner.privateKey,
    identityId,
    username: 'CryptoBot',
    clientPublicKey: cpk,
    now: 1_800_000_000,
    tokenId: 'b26f0455-9505-4c4e-a3f0-a1c72b58b8ef'
  })
  const verified = verifyCompact(token, owner.publicKey)
  assert.equal(verified.valid, true)
  assert.equal(verified.header.alg, 'ES384')
  assert.equal(verified.claims.sub, identityId)
  assert.equal(verified.claims.xname, 'CryptoBot')
  assert.equal(verified.claims.cpk, cpk)
  assert.equal(verified.claims.xid, undefined)
  assert.equal(verified.claims.XUID, undefined)
})

test('tampering invalidates the owner signature', () => {
  const owner = crypto.generateKeyPairSync('ec', { namedCurve: 'secp384r1' })
  const client = crypto.generateKeyPairSync('ec', { namedCurve: 'secp384r1' })
  const token = createLocalIdentityToken({
    privateKey: owner.privateKey,
    identityId: '63572362-0c83-5f0a-8cec-e1b788101798',
    username: 'TamperBot',
    clientPublicKey: client.publicKey.export({ type: 'spki', format: 'der' }).toString('base64')
  })
  assert.equal(verifyCompact(tamperPayload(token, { xname: 'Attacker' }), owner.publicKey).valid, false)
})

test('authflow emits fresh tokens with one stable hidden identity', async () => {
  const directory = temporaryDirectory('endbot-flow-')
  const auth = createLocalOwnerbotAuth({
    username: 'FlowBot',
    identityId: loadOrCreateIdentityId(path.join(directory, 'bot.uuid')),
    privateKeyPath: path.join(directory, 'private.pem'),
    publicKeyPath: path.join(directory, 'public.pem')
  })
  const client = crypto.generateKeyPairSync('ec', { namedCurve: 'secp384r1' })
  const cpk = client.publicKey.export({ type: 'spki', format: 'der' }).toString('base64')
  const first = await auth.authflow.getMinecraftBedrockToken(cpk)
  const second = await auth.authflow.getMinecraftBedrockToken(cpk)
  const ownerPublic = crypto.createPublicKey(fs.readFileSync(path.join(directory, 'public.pem')))
  const firstClaims = verifyCompact(first.token, ownerPublic).claims
  const secondClaims = verifyCompact(second.token, ownerPublic).claims
  assert.equal(firstClaims.sub, auth.identityId)
  assert.equal(secondClaims.sub, auth.identityId)
  assert.notEqual(firstClaims.jti, secondClaims.jti)
  assert.equal(first.chain.length, 2)
  const profile = verifyCompact(first.chain[1], ownerPublic)
  assert.equal(profile.valid, true)
  assert.equal(profile.claims.extraData.displayName, 'FlowBot')
  assert.equal(profile.claims.extraData.identity, auth.identityId)
  assert.equal(profile.claims.extraData.XUID, undefined)
})

test('rejects invalid identity inputs before signing', () => {
  assert.throws(() => createLocalOwnerbotAuth({
    username: '',
    identityId: '63572362-0c83-5f0a-8cec-e1b788101798',
    privateKeyPath: 'unused.pem'
  }), /between 1 and 64/)
  const owner = crypto.generateKeyPairSync('ec', { namedCurve: 'secp384r1' })
  assert.throws(() => createLocalIdentityToken({
    privateKey: owner.privateKey,
    identityId: 'not-a-uuid',
    username: 'Bot',
    clientPublicKey: 'not-a-key'
  }), /RFC 4122 UUID/)
})
