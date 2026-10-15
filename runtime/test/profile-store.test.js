// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { ProfileStore } from '../src/profile-store.js'

function store () {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'endbot-profiles-'))
  test.after(() => fs.rmSync(directory, { recursive: true }))
  return new ProfileStore(directory)
}

test('creates profiles with immutable UUIDs and reuses persisted state', () => {
  const profiles = store()
  const alice = profiles.create('Alice')
  const updated = profiles.update(alice.identityId, { desiredOnline: true })
  const reloaded = new ProfileStore(profiles.directory).getByName('alice')
  assert.equal(updated.identityId, alice.identityId)
  assert.deepEqual(reloaded, updated)
})

test('rename preserves UUID and rejects case-insensitive collisions', () => {
  const profiles = store()
  const alice = profiles.create('Alice')
  profiles.create('Bob')
  const renamed = profiles.update(alice.identityId, { name: 'Builder' })
  assert.equal(renamed.identityId, alice.identityId)
  assert.equal(profiles.getByName('Alice'), undefined)
  assert.equal(profiles.getByName('builder').identityId, alice.identityId)
  assert.throws(() => profiles.update(alice.identityId, { name: 'BOB' }), error => error.code === 'name_conflict')
})

test('corrupt profiles and symlink entries fail closed', (t) => {
  const profiles = store()
  fs.writeFileSync(path.join(profiles.directory, 'bad.json'), '{}')
  assert.throws(() => new ProfileStore(profiles.directory), /schema/)
  fs.rmSync(path.join(profiles.directory, 'bad.json'))
  const target = path.join(profiles.directory, 'target')
  fs.writeFileSync(target, '{}')
  try {
    fs.symlinkSync(target, path.join(profiles.directory, 'linked.json'), process.platform === 'win32' ? 'file' : undefined)
  } catch (error) {
    if (process.platform === 'win32' && ['EPERM', 'EACCES'].includes(error.code)) return t.skip('Windows symlink privilege unavailable')
    throw error
  }
  assert.throws(() => new ProfileStore(profiles.directory), /Invalid profile entry/)
})

test('forget removes only the selected profile', () => {
  const profiles = store()
  const alice = profiles.create('Alice')
  const bob = profiles.create('Bob')
  profiles.remove(alice.identityId)
  assert.equal(profiles.getByName('Alice'), undefined)
  assert.equal(profiles.getById(bob.identityId).name, 'Bob')
})

test('optional chunk radius persists through reload and rename and can return to inheritance', () => {
  const profiles = store()
  const alice = profiles.create('Alice')
  assert.equal('chunkRadius' in alice, false)
  profiles.update(alice.identityId, { chunkRadius: 12 })
  const reloaded = new ProfileStore(profiles.directory)
  const renamed = reloaded.update(alice.identityId, { name: 'Builder' })
  assert.equal(renamed.chunkRadius, 12)
  reloaded.update(alice.identityId, { chunkRadius: undefined })
  assert.equal('chunkRadius' in new ProfileStore(profiles.directory).getByName('Builder'), false)
  const persisted = JSON.parse(fs.readFileSync(path.join(profiles.directory, `${alice.identityId}.json`)))
  assert.equal('chunkRadius' in persisted, false)
})

test('invalid persisted chunk radius is rejected without overwriting the profile', () => {
  const profiles = store()
  const alice = profiles.create('Alice')
  for (const chunkRadius of [null, 1, 33, '12', 2.5, true]) {
    assert.throws(() => profiles.update(alice.identityId, { chunkRadius }), /chunkRadius/)
    assert.equal('chunkRadius' in profiles.getById(alice.identityId), false)
  }
  const filename = path.join(profiles.directory, `${alice.identityId}.json`)
  fs.writeFileSync(filename, JSON.stringify({ ...alice, chunkRadius: 0 }))
  assert.throws(() => new ProfileStore(profiles.directory), /chunkRadius/)
})
