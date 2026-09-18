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
