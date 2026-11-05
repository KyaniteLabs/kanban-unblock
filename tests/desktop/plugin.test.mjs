// Loads desktop/plugin.js the way the desktop disk door does (ESM, only the
// three allowed specifiers), with a stub SDK, and drives it in jsdom.
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { JSDOM } from 'jsdom'

const here = dirname(fileURLToPath(import.meta.url))
const pluginPath = join(here, '..', '..', 'plugins', 'kanban-unblock-hermes', 'desktop', 'plugin.js')

const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/#/' })
globalThis.window = dom.window
globalThis.document = dom.window.document
globalThis.navigator = dom.window.navigator
globalThis.HTMLElement = dom.window.HTMLElement
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const source = readFileSync(pluginPath, 'utf8')
const specifiers = [...source.matchAll(/from '([^']+)'/g)].map(m => m[1])
const allowed = new Set(['@hermes/plugin-sdk', 'react', 'react/jsx-runtime'])
const shimPath = join(here, '.plugin.under-test.mjs')
writeFileSync(shimPath, source.replace("from '@hermes/plugin-sdk'", "from './sdk-stub.mjs'"))

const sdk = await import('./sdk-stub.mjs')
const mod = await import(pathToFileURL(shimPath).href)
const { act } = await import('react')
const { createRoot } = await import('react-dom/client')

function fakeCtx() {
  const contributions = new Map()
  const disposers = []
  const store = new Map()
  const restCalls = []
  return {
    contributions,
    disposers,
    restCalls,
    register: c => {
      contributions.set(c.id, c)
      return () => contributions.delete(c.id)
    },
    rest: (path, opts) => {
      restCalls.push([path, opts])
      return Promise.resolve(path.startsWith('/answer') ? { ok: true, status: 'ready', unblocked: true } : { ok: true, tasks: [] })
    },
    storage: { get: (k, d) => (store.has(k) ? store.get(k) : d), set: (k, v) => store.set(k, v) },
    onDispose: fn => disposers.push(fn)
  }
}

const go = hash => {
  window.location.hash = hash
  window.dispatchEvent(new window.HashChangeEvent('hashchange'))
}

test('only the three allowed specifiers are imported', () => {
  assert.deepEqual(specifiers.filter(s => !allowed.has(s)), [])
  assert.equal(mod.default.id, 'kanban-unblock')
})

test('route detection', () => {
  assert.ok(mod.isKanbanRoute('#/kanban'))
  assert.ok(mod.isKanbanRoute('#/kanban?board=x'))
  assert.ok(!mod.isKanbanRoute('#/kanban-unblock'))
  assert.ok(!mod.isKanbanRoute('#/settings'))
})

test('header control exists only while /kanban is on screen', () => {
  go('#/')
  const ctx = fakeCtx()
  mod.default.register(ctx)
  assert.ok(ctx.contributions.has('open'), 'palette command registered')
  assert.ok(!ctx.contributions.has('needs-input'))
  go('#/kanban')
  assert.equal(ctx.contributions.get('needs-input').area, 'workspace.pageHeader')
  go('#/skills')
  assert.ok(!ctx.contributions.has('needs-input'))
  ctx.disposers.forEach(d => d())
})

test('palette command navigates to /kanban and opens the panel', () => {
  go('#/')
  const ctx = fakeCtx()
  mod.default.register(ctx)
  ctx.contributions.get('open').data.run()
  assert.equal(window.location.hash, '#/kanban')
  assert.ok(ctx.contributions.has('needs-input'))
  assert.equal(mod.$open.get(), true)
  ctx.disposers.forEach(d => d())
  mod.$open.set(false)
})

test('answer in place: question shown, POST /answer, native board invalidated', async () => {
  go('#/kanban')
  const ctx = fakeCtx()
  mod.default.register(ctx)
  sdk.queryState.data = {
    ok: true,
    count: 1,
    tasks: [{ task_id: 't1', title: 'Pick a DB', question: 'Postgres or SQLite?', recent_comments: [{ author: 'w', body: 'need this' }] }],
    excluded: [{ task_id: 't2', block_kind: 'capability' }],
    errors: []
  }
  const root = createRoot(document.getElementById('root'))
  await act(async () => root.render(ctx.contributions.get('needs-input').render()))
  const text = document.body.textContent
  assert.match(text, /Needs input/)
  assert.match(text, /Q: Postgres or SQLite\?/)
  assert.match(text, /1 other blocked card\(s\) out of scope/)

  const area = document.querySelector('textarea[aria-label="Answer for t1"]')
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
  await act(async () => {
    setter.call(area, 'SQLite')
    area.dispatchEvent(new window.Event('input', { bubbles: true }))
  })
  const button = [...document.querySelectorAll('button')].find(b => b.textContent === 'Answer & unblock')
  assert.equal(button.disabled, false)
  await act(async () => button.dispatchEvent(new window.MouseEvent('click', { bubbles: true })))

  const answer = ctx.restCalls.find(([path]) => path === '/answer')
  assert.ok(answer, 'POST /answer sent')
  assert.equal(answer[1].method, 'POST')
  assert.deepEqual(answer[1].body, { board: undefined, mode: 'clarify', task_id: 't1', text: 'SQLite' })
  assert.ok(sdk.calls.invalidated.some(k => k.join('/') === 'kanban-unblock/queue'))
  assert.ok(sdk.calls.invalidated.some(k => k.length === 1 && k[0] === 'kanban'), 'native board refreshed')
  assert.equal(sdk.calls.notified.at(-1).kind, 'success')
  await act(async () => root.unmount())
  ctx.disposers.forEach(d => d())
})

test('partial failure is reported, not hidden', async () => {
  const api = { answer: async () => ({ ok: false, partial: true, error: 'answer saved but unblock failed: locked' }) }
  const res = await mod.submitAnswer(api, { mode: 'clarify', taskId: 't1', text: 'x' })
  assert.equal(mod.describeResult('clarify', res), 'Partial: answer saved but unblock failed: locked')
  const thrown = await mod.submitAnswer({ answer: async () => { throw new Error('offline') } }, { mode: 'reply', taskId: 't1', text: 'x' })
  assert.equal(thrown.ok, false)
  assert.equal(mod.describeResult('reply', thrown), 'offline')
})

test('board selection goes to the backend', async () => {
  const ctx = fakeCtx()
  const api = mod.createApi(ctx)
  api.setBoard(' team-a ')
  await api.queue()
  await api.answer('t1', 'reply', 'hi')
  assert.equal(ctx.restCalls[0][0], '/queue?board=team-a')
  assert.equal(ctx.restCalls[1][1].body.board, 'team-a')
})
