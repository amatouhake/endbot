#!/usr/bin/env node
// Copyright 2026 amatouhake and Endbot contributors
// SPDX-License-Identifier: Apache-2.0

import path from 'node:path'

import { loadConfig } from './config.js'
import { ControlServer } from './control-server.js'
import { BotLifecycle } from './lifecycle.js'
import { loadOrCreateControlToken } from './local-identity.js'
import { ProfileStore } from './profile-store.js'
import { createSessionFactory } from './protocol-session.js'

const configIndex = process.argv.indexOf('--config')
const configPath = configIndex >= 0 ? process.argv[configIndex + 1] : 'endbot-runtime.json'
if (!configPath) throw new Error('--config requires a filename')

const config = loadConfig(configPath)
const token = loadOrCreateControlToken(config.controlTokenPath)
const store = new ProfileStore(path.join(config.dataDirectory, 'profiles'))
const lifecycle = new BotLifecycle({ store, sessionFactory: createSessionFactory(config), reconnect: config.reconnect })
const control = new ControlServer({ host: config.controlHost, port: config.controlPort, token, lifecycle })

await control.listen()
await lifecycle.start()
process.stdout.write(`${JSON.stringify({ event: 'endbot_runtime_ready', host: config.controlHost, port: control.port })}\n`)

let stopping = false
async function stop () {
  if (stopping) return
  stopping = true
  await control.close()
  await lifecycle.close()
}
process.on('SIGINT', () => { void stop().then(() => process.exit(0)) })
process.on('SIGTERM', () => { void stop().then(() => process.exit(0)) })
