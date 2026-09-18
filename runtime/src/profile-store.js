// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

import { loadOrCreatePersistentArtifact } from './local-identity.js'

const PROFILE_VERSION = 1
const NAME_PATTERN = /^[A-Za-z0-9_]{1,16}$/

export class ProfileError extends Error {
  constructor (code, message) {
    super(message)
    this.code = code
  }
}

export function validateBotName (name) {
  if (typeof name !== 'string' || !NAME_PATTERN.test(name)) {
    throw new ProfileError('invalid_name', 'Bot names must contain 1-16 letters, digits, or underscores')
  }
  return name
}

function validateProfile (value) {
  if (!value || value.schemaVersion !== PROFILE_VERSION) throw new Error('Unsupported Endbot profile schema')
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.identityId)) {
    throw new Error('Endbot profile identityId is not a UUID v4')
  }
  validateBotName(value.name)
  if (typeof value.desiredOnline !== 'boolean') throw new Error('Endbot profile desiredOnline must be boolean')
  return Object.freeze({
    schemaVersion: PROFILE_VERSION,
    identityId: value.identityId.toLowerCase(),
    name: value.name,
    desiredOnline: value.desiredOnline
  })
}

function parseProfileFile (filename) {
  const metadata = fs.lstatSync(filename)
  if (!metadata.isFile() || metadata.isSymbolicLink()) throw new Error('Endbot profile path must be a regular file')
  return validateProfile(JSON.parse(fs.readFileSync(filename, 'utf8')))
}

function serializeProfile (profile) {
  return `${JSON.stringify(profile, null, 2)}\n`
}

function syncDirectory (directory) {
  if (process.platform === 'win32') return
  const descriptor = fs.openSync(directory, 'r')
  try { fs.fsyncSync(descriptor) } finally { fs.closeSync(descriptor) }
}

function atomicReplace (filename, contents) {
  const directory = path.dirname(filename)
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 })
  const temporary = `${filename}.${process.pid}.${crypto.randomBytes(6).toString('hex')}.tmp`
  try {
    const descriptor = fs.openSync(temporary, 'wx', 0o600)
    try {
      fs.writeFileSync(descriptor, contents)
      fs.fsyncSync(descriptor)
    } finally {
      fs.closeSync(descriptor)
    }
    fs.renameSync(temporary, filename)
    if (process.platform !== 'win32') fs.chmodSync(filename, 0o600)
    syncDirectory(directory)
  } finally {
    fs.rmSync(temporary, { force: true })
  }
}

export class ProfileStore {
  constructor (directory) {
    this.directory = path.resolve(directory)
    this.byId = new Map()
    this.byName = new Map()
    this.load()
  }

  load () {
    fs.mkdirSync(this.directory, { recursive: true, mode: 0o700 })
    this.byId.clear()
    this.byName.clear()
    for (const entry of fs.readdirSync(this.directory, { withFileTypes: true })) {
      if (!entry.name.endsWith('.json')) continue
      if (!entry.isFile() || entry.isSymbolicLink()) throw new Error(`Invalid profile entry: ${entry.name}`)
      const profile = parseProfileFile(path.join(this.directory, entry.name))
      if (`${profile.identityId}.json` !== entry.name) throw new Error(`Profile filename does not match identity: ${entry.name}`)
      const normalized = profile.name.toLowerCase()
      if (this.byId.has(profile.identityId) || this.byName.has(normalized)) throw new Error('Duplicate Endbot profile identity or name')
      this.byId.set(profile.identityId, profile)
      this.byName.set(normalized, profile.identityId)
    }
  }

  list () {
    return [...this.byId.values()].sort((a, b) => a.name.localeCompare(b.name))
  }

  getByName (name) {
    const identityId = this.byName.get(String(name).toLowerCase())
    return identityId ? this.byId.get(identityId) : undefined
  }

  getById (identityId) { return this.byId.get(String(identityId).toLowerCase()) }

  create (name) {
    validateBotName(name)
    if (this.getByName(name)) throw new ProfileError('name_conflict', `A Bot named ${name} already exists`)
    const candidate = validateProfile({
      schemaVersion: PROFILE_VERSION,
      identityId: crypto.randomUUID(),
      name,
      desiredOnline: false
    })
    const filename = path.join(this.directory, `${candidate.identityId}.json`)
    const persisted = loadOrCreatePersistentArtifact(
      filename,
      () => serializeProfile(candidate),
      parseProfileFile,
      'Endbot profile'
    ).value
    // A single controller owns name mutation. Re-check protects accidental
    // concurrent controller startup from admitting an ambiguous name.
    this.load()
    const winner = this.getByName(name)
    if (!winner || winner.identityId !== persisted.identityId) {
      throw new ProfileError('name_conflict', `A Bot named ${name} was created concurrently`)
    }
    return winner
  }

  update (identityId, changes) {
    const current = this.getById(identityId)
    if (!current) throw new ProfileError('not_found', 'Bot profile does not exist')
    const next = validateProfile({ ...current, ...changes, identityId: current.identityId })
    if (next.name.toLowerCase() !== current.name.toLowerCase()) {
      const collision = this.getByName(next.name)
      if (collision && collision.identityId !== current.identityId) {
        throw new ProfileError('name_conflict', `A Bot named ${next.name} already exists`)
      }
    }
    atomicReplace(path.join(this.directory, `${current.identityId}.json`), serializeProfile(next))
    this.byId.set(current.identityId, next)
    this.byName.delete(current.name.toLowerCase())
    this.byName.set(next.name.toLowerCase(), current.identityId)
    return next
  }

  remove (identityId) {
    const current = this.getById(identityId)
    if (!current) throw new ProfileError('not_found', 'Bot profile does not exist')
    fs.unlinkSync(path.join(this.directory, `${current.identityId}.json`))
    syncDirectory(this.directory)
    this.byId.delete(current.identityId)
    this.byName.delete(current.name.toLowerCase())
  }
}
