// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import { fork } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

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

const workerPath = fileURLToPath(new URL('../test-support/persistent-artifact-worker.js', import.meta.url))

function concurrentWorkers (kind, filename, count = 12) {
  const children = Array.from({ length: count }, () => {
    const child = fork(workerPath, [kind, filename], { silent: true })
    let stderr = ''
    child.stderr.on('data', (chunk) => { stderr += chunk })

    let readyResolve
    const ready = new Promise((resolve) => { readyResolve = resolve })
    const result = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        child.kill()
        reject(new Error(`Persistent artifact worker timed out: ${stderr}`))
      }, 15_000)
      child.on('message', (message) => {
        if (message.ready) {
          readyResolve()
        } else {
          clearTimeout(timeout)
          if (message.ok) resolve(message)
          else reject(new Error(message.error))
        }
      })
      child.on('error', reject)
      child.on('exit', (code) => {
        if (code !== 0) reject(new Error(`Persistent artifact worker exited ${code}: ${stderr}`))
      })
    }).finally(() => child.disconnect())
    return { child, ready, result }
  })

  return Promise.all(children.map(({ ready }) => ready)).then(() => {
    for (const { child } of children) child.send({ start: true })
    return Promise.all(children.map(({ result }) => result))
  })
}

function waitForFile (filename, timeoutMilliseconds = 15_000) {
  const deadline = Date.now() + timeoutMilliseconds
  return new Promise((resolve, reject) => {
    const poll = () => {
      if (fs.existsSync(filename)) return resolve()
      if (Date.now() >= deadline) return reject(new Error(`Timed out waiting for ${filename}`))
      setTimeout(poll, 5)
    }
    poll()
  })
}

async function interruptPublisherBeforeCommit (kind, filename) {
  const marker = `${filename}.pause-marker`
  const child = fork(workerPath, [kind, filename, marker], {
    silent: true,
    env: { ...process.env, ENDBOT_TEST_PAUSE_BEFORE_PUBLISH: '1' }
  })
  let stderr = ''
  child.stderr.on('data', (chunk) => { stderr += chunk })
  const exited = new Promise((resolve) => child.once('exit', resolve))
  const ready = new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`Interrupted publisher did not become ready: ${stderr}`)), 15_000)
    child.on('message', (message) => {
      if (!message.ready) return
      clearTimeout(timeout)
      resolve()
    })
    child.once('error', reject)
  })

  await ready
  child.send({ start: true })
  await waitForFile(marker)
  child.kill()
  await exited
  fs.rmSync(marker, { force: true })
}

function createSymlinkOrSkip (t, target, link) {
  try {
    fs.symlinkSync(target, link, process.platform === 'win32' ? 'file' : undefined)
    return true
  } catch (error) {
    if (process.platform === 'win32' && ['EPERM', 'EACCES'].includes(error.code)) {
      t.skip('Creating symlinks requires Windows Developer Mode or elevated privileges')
      return false
    }
    throw error
  }
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

test('refuses a symlink as a private-key path', (t) => {
  const directory = temporaryDirectory('endbot-key-link-')
  const real = path.join(directory, 'real.pem')
  loadOrCreateOwnerKeyPair(real)
  const link = path.join(directory, 'linked.pem')
  if (!createSymlinkOrSkip(t, real, link)) return
  assert.throws(() => loadOrCreateOwnerKeyPair(link), /regular file, not a symlink/)
})

test('concurrent processes converge on one persisted owner key', async () => {
  const directory = temporaryDirectory('endbot-key-race-')
  const filename = path.join(directory, 'owner-private.pem')
  const results = await concurrentWorkers('owner-key', filename)

  const persistedPrivateKey = crypto.createPrivateKey(fs.readFileSync(filename))
  const persistedPublicKey = crypto.createPublicKey(persistedPrivateKey)
    .export({ type: 'spki', format: 'der' })
  const persistedFingerprint = crypto.createHash('sha256').update(persistedPublicKey).digest('hex')
  assert.deepEqual(new Set(results.map(({ value }) => value)), new Set([persistedFingerprint]))
  assert.equal(results.filter(({ created }) => created).length, 1)
  assert.deepEqual(fs.readdirSync(directory), ['owner-private.pem'])
})

test('interrupted owner-key publication never exposes a partial final file', async () => {
  const directory = temporaryDirectory('endbot-key-interrupted-')
  const filename = path.join(directory, 'owner-private.pem')
  await interruptPublisherBeforeCommit('owner-key', filename)
  assert.equal(fs.existsSync(filename), false)

  const keys = loadOrCreateOwnerKeyPair(filename)
  const persistedPrivateKey = crypto.createPrivateKey(fs.readFileSync(filename))
  const persistedPublicKey = crypto.createPublicKey(persistedPrivateKey)
    .export({ type: 'spki', format: 'der' }).toString('base64')
  assert.equal(keys.publicKeyDerBase64, persistedPublicKey)
  assert.deepEqual(fs.readdirSync(directory), ['owner-private.pem'])
})

test('refuses to replace a corrupt owner key', () => {
  const directory = temporaryDirectory('endbot-key-corrupt-')
  const filename = path.join(directory, 'owner-private.pem')
  fs.writeFileSync(filename, 'not a private key\n')
  assert.throws(() => loadOrCreateOwnerKeyPair(filename))
  assert.equal(fs.readFileSync(filename, 'utf8'), 'not a private key\n')
})

test('persists an identity UUID independently from the user-visible name', () => {
  const directory = temporaryDirectory('endbot-identity-')
  const filename = path.join(directory, 'bot.uuid')
  const identityId = loadOrCreateIdentityId(filename)
  assert.equal(loadOrCreateIdentityId(filename), identityId)
  assert.match(identityId, /^[0-9a-f-]{36}$/)
  if (process.platform !== 'win32') assert.equal(fs.statSync(filename).mode & 0o777, 0o600)
})

test('concurrent processes converge on one persisted identity UUID', async () => {
  const directory = temporaryDirectory('endbot-identity-race-')
  const filename = path.join(directory, 'bot.uuid')
  const results = await concurrentWorkers('identity-id', filename)
  const persistedIdentity = fs.readFileSync(filename, 'utf8').trim()

  assert.deepEqual(new Set(results.map(({ value }) => value)), new Set([persistedIdentity]))
  assert.match(persistedIdentity, /^[0-9a-f-]{36}$/)
  assert.deepEqual(fs.readdirSync(directory), ['bot.uuid'])
})

test('interrupted identity publication never exposes a partial final file', async () => {
  const directory = temporaryDirectory('endbot-identity-interrupted-')
  const filename = path.join(directory, 'bot.uuid')
  await interruptPublisherBeforeCommit('identity-id', filename)
  assert.equal(fs.existsSync(filename), false)

  const identityId = loadOrCreateIdentityId(filename)
  assert.equal(identityId, fs.readFileSync(filename, 'utf8').trim())
  assert.deepEqual(fs.readdirSync(directory), ['bot.uuid'])
})

test('refuses to replace a corrupt identity UUID', () => {
  const directory = temporaryDirectory('endbot-identity-corrupt-')
  const filename = path.join(directory, 'bot.uuid')
  fs.writeFileSync(filename, 'not-a-uuid\n')
  assert.throws(() => loadOrCreateIdentityId(filename), /RFC 4122 UUID/)
  assert.equal(fs.readFileSync(filename, 'utf8'), 'not-a-uuid\n')
})

test('refuses a symlink as an identity path', (t) => {
  const directory = temporaryDirectory('endbot-identity-link-')
  const real = path.join(directory, 'real.uuid')
  loadOrCreateIdentityId(real)
  const link = path.join(directory, 'linked.uuid')
  if (!createSymlinkOrSkip(t, real, link)) return
  assert.throws(() => loadOrCreateIdentityId(link), /regular file, not a symlink/)
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

test('rejects normalized private/public key path overlap without damaging the private key', () => {
  const directory = temporaryDirectory('endbot-key-overlap-')
  const privateKeyPath = path.join(directory, 'owner.pem')
  const nested = path.join(directory, 'nested')
  fs.mkdirSync(nested)
  const original = loadOrCreateOwnerKeyPair(privateKeyPath)
  const originalPem = fs.readFileSync(privateKeyPath)

  assert.throws(() => createLocalOwnerbotAuth({
    username: 'OverlapBot',
    identityId: '63572362-0c83-5f0a-8cec-e1b788101798',
    privateKeyPath,
    publicKeyPath: path.join(nested, '..', 'owner.pem')
  }), /private-key and public-key paths must be different/)

  assert.deepEqual(fs.readFileSync(privateKeyPath), originalPem)
  assert.equal(loadOrCreateOwnerKeyPair(privateKeyPath).publicKeyDerBase64, original.publicKeyDerBase64)
})

test('rejects private/public key hard-link aliases without damaging the private key', () => {
  const directory = temporaryDirectory('endbot-key-hardlink-overlap-')
  const privateKeyPath = path.join(directory, 'owner.pem')
  const publicKeyPath = path.join(directory, 'owner-public.pem')
  const original = loadOrCreateOwnerKeyPair(privateKeyPath)
  const originalPem = fs.readFileSync(privateKeyPath)
  fs.linkSync(privateKeyPath, publicKeyPath)

  assert.throws(() => createLocalOwnerbotAuth({
    username: 'HardLinkBot',
    identityId: '63572362-0c83-5f0a-8cec-e1b788101798',
    privateKeyPath,
    publicKeyPath
  }), /must not refer to the same file/)

  assert.deepEqual(fs.readFileSync(privateKeyPath), originalPem)
  assert.equal(loadOrCreateOwnerKeyPair(privateKeyPath).publicKeyDerBase64, original.publicKeyDerBase64)
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
