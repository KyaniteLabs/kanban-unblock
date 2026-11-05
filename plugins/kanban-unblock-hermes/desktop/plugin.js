/**
 * Kanban Unblock — desktop half (unified package: plugins/<id>/desktop/plugin.js).
 *
 * Answer-in-place INSIDE the native /kanban page: while the /kanban route is
 * on screen, a "Needs input · N" control is projected into the Kanban page
 * header (WORKSPACE_PAGE_HEADER_AREA, beside the board switcher). It opens a
 * panel listing blocked cards with block_kind=needs_input, shows each card's
 * question + recent comments, and lets the operator answer → comment +
 * unblock (or comment only) without leaving the board. After every action
 * the native board's queries are invalidated so the card moves lanes live.
 *
 * Backend: dashboard/plugin_api.py via ctx.rest (/queue, /answer). Shell/code
 * approvals are out of scope (catalog needs-you); the backend refuses them.
 *
 * Plain ESM, no build step: only @hermes/plugin-sdk, react, react/jsx-runtime.
 */

import {
  atom,
  Badge,
  Button,
  cn,
  host,
  Input,
  PALETTE_AREA,
  Popover,
  PopoverContent,
  PopoverTrigger,
  queryClient,
  Textarea,
  useQuery,
  useValue,
  WORKSPACE_PAGE_HEADER_AREA
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

export const PLUGIN_ID = 'kanban-unblock'
export const QUEUE_KEY = ['kanban-unblock', 'queue']
const NATIVE_KANBAN_KEY = ['kanban']
const POLL_MS = 8000

/** Popover open state — shared so the ⌘K command can open it. */
export const $open = atom(false)

/** True while the app router shows the native Kanban page. */
export function isKanbanRoute(hash = typeof window === 'undefined' ? '' : window.location.hash) {
  const path = String(hash || '').replace(/^#/, '').split('?')[0]

  return path === '/kanban' || path.startsWith('/kanban/')
}

/** Thin client over this plugin's backend namespace (/api/plugins/kanban-unblock). */
export function createApi(ctx) {
  const board = () => String(ctx.storage.get('board', '') || '').trim()

  return {
    board,
    setBoard: value => ctx.storage.set('board', String(value || '').trim()),
    queue: () => {
      const slug = board()

      return ctx.rest(slug ? `/queue?board=${encodeURIComponent(slug)}` : '/queue')
    },
    answer: (taskId, mode, text) =>
      ctx.rest('/answer', {
        method: 'POST',
        body: { board: board() || undefined, mode, task_id: taskId, text }
      })
  }
}

/** Submit one answer, then refresh our queue AND the native board (live). */
export async function submitAnswer(api, { mode, taskId, text }) {
  let result

  try {
    result = await api.answer(taskId, mode, text)
  } catch (error) {
    result = { error: String((error && error.message) || error), ok: false }
  }

  // Invalidate even on failure: a partial success (comment landed, unblock
  // failed) still changed the card.
  void queryClient.invalidateQueries({ queryKey: QUEUE_KEY })
  void queryClient.invalidateQueries({ queryKey: NATIVE_KANBAN_KEY })

  return result
}

export function describeResult(mode, result) {
  if (result && result.ok) {
    return mode === 'reply' ? 'Comment posted — card stays blocked.' : `Answered and unblocked${result.status ? ` → ${result.status}` : ''}.`
  }

  const error = (result && result.error) || 'Unknown error'

  return result && result.partial ? `Partial: ${error}` : error
}

function CardItem({ api, card }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)

  const act = async mode => {
    if (busy || !text.trim()) {
      return
    }

    setBusy(true)
    const result = await submitAnswer(api, { mode, taskId: card.task_id, text: text.trim() })
    setBusy(false)
    const message = describeResult(mode, result)
    setNote({ error: !result.ok, message })

    if (result.ok) {
      setText('')
      host.notify({ kind: 'success', message: `${card.task_id}: ${message}` })
    } else {
      host.notify({ kind: 'error', message: `${card.task_id}: ${message}` })
    }
  }

  const comments = Array.isArray(card.recent_comments) ? card.recent_comments : []

  return jsxs('div', {
    className: 'flex flex-col gap-1.5 border-b border-(--ui-stroke-secondary) px-3 py-2.5 last:border-b-0',
    children: [
      jsxs('div', {
        className: 'flex items-baseline gap-2',
        children: [
          jsx('span', { className: 'font-mono text-[0.6875rem] text-(--ui-text-tertiary)', children: card.task_id }),
          jsx('span', { className: 'truncate text-xs font-medium', children: card.title || '(untitled)' })
        ]
      }),
      card.question
        ? jsx('div', {
            className: 'whitespace-pre-wrap text-xs text-(--ui-text-secondary)',
            children: `Q: ${card.question}`
          })
        : null,
      comments.length
        ? jsx('div', {
            className: 'flex flex-col gap-0.5 text-[0.6875rem] text-(--ui-text-tertiary)',
            children: comments.map((c, i) =>
              jsx('div', { className: 'truncate', children: `${c.author || '?'}: ${c.body || ''}` }, i)
            )
          })
        : null,
      jsx(Textarea, {
        'aria-label': `Answer for ${card.task_id}`,
        className: 'min-h-16 text-xs',
        disabled: busy,
        onChange: e => setText(e.target.value),
        onKeyDown: e => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault()
            void act('clarify')
          }
        },
        placeholder: 'Your answer… (⌘↵ answers & unblocks)',
        value: text
      }),
      jsxs('div', {
        className: 'flex items-center justify-end gap-1.5',
        children: [
          note
            ? jsx('span', {
                className: cn('mr-auto text-[0.6875rem]', note.error ? 'text-destructive' : 'text-(--ui-text-tertiary)'),
                children: note.message
              })
            : null,
          jsx(Button, {
            disabled: busy || !text.trim(),
            onClick: () => void act('reply'),
            size: 'xs',
            variant: 'ghost',
            children: 'Comment only'
          }),
          jsx(Button, {
            disabled: busy || !text.trim(),
            onClick: () => void act('clarify'),
            size: 'xs',
            children: busy ? 'Sending…' : 'Answer & unblock'
          })
        ]
      })
    ]
  })
}

function AnswerPanel({ api }) {
  const [board, setBoard] = useState(api.board())
  const query = useQuery({ queryFn: () => api.queue(), queryKey: [...QUEUE_KEY, board], refetchInterval: POLL_MS })
  const data = query.data
  const stale = data && !data.ok && data.stale && Array.isArray(data.stale.tasks) ? data.stale : null
  const tasks = (data && data.ok && Array.isArray(data.tasks) && data.tasks) || (stale && stale.tasks) || []
  const error = query.error ? String(query.error.message || query.error) : data && !data.ok ? data.error : null

  return jsxs('div', {
    className: 'flex max-h-[70vh] w-[26rem] flex-col text-sm',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2 border-b border-(--ui-stroke-secondary) px-3 py-2',
        children: [
          jsx('span', { className: 'text-xs font-medium', children: 'Needs input' }),
          jsx('span', {
            className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
            children: 'blocked · needs_input only — shell/code approvals: needs-you'
          }),
          jsx(Input, {
            'aria-label': 'Board slug',
            className: 'ml-auto h-6 w-28 text-[0.6875rem]',
            onBlur: () => api.setBoard(board),
            onChange: e => setBoard(e.target.value),
            placeholder: 'current board',
            value: board
          })
        ]
      }),
      error
        ? jsx('div', {
            className: 'px-3 py-2 text-xs text-destructive',
            children: `Queue unavailable: ${error}${stale ? ' — showing the last snapshot (stale)' : ''}`
          })
        : null,
      query.isLoading ? jsx('div', { className: 'px-3 py-3 text-xs text-(--ui-text-tertiary)', children: 'Loading…' }) : null,
      !query.isLoading && !error && tasks.length === 0
        ? jsx('div', {
            className: 'px-3 py-3 text-xs text-(--ui-text-tertiary)',
            children: 'Nothing waiting on you. Cards blocked with kind needs_input show up here.'
          })
        : null,
      jsx('div', {
        className: 'flex-1 overflow-y-auto',
        children: tasks.map(card => jsx(CardItem, { api, card }, card.task_id))
      }),
      data && data.ok && (data.partial || (data.excluded && data.excluded.length))
        ? jsx('div', {
            className: 'border-t border-(--ui-stroke-secondary) px-3 py-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
            children: [
              data.excluded && data.excluded.length ? `${data.excluded.length} other blocked card(s) out of scope.` : '',
              data.partial ? ` ${data.errors.length} card(s) could not be inspected.` : ''
            ].join('')
          })
        : null
    ]
  })
}

function NeedsInputControl({ api }) {
  const open = useValue($open)
  const query = useQuery({ queryFn: () => api.queue(), queryKey: [...QUEUE_KEY, api.board()], refetchInterval: POLL_MS })
  const count = query.data && query.data.ok ? query.data.count || 0 : 0

  return jsxs(Popover, {
    onOpenChange: value => $open.set(value),
    open,
    children: [
      jsx(PopoverTrigger, {
        asChild: true,
        children: jsxs(Button, {
          'aria-label': 'Kanban: cards that need your input',
          size: 'xs',
          variant: count ? 'secondary' : 'ghost',
          children: ['Needs input', jsx(Badge, { children: String(count) })]
        })
      }),
      jsx(PopoverContent, { align: 'end', className: 'p-0', children: jsx(AnswerPanel, { api }) })
    ]
  })
}

export default {
  id: PLUGIN_ID,
  name: 'Kanban Unblock',
  description: 'Answer blocked needs_input cards in place inside /kanban (not shell approvals).',
  register(ctx) {
    const api = createApi(ctx)
    let disposeHeader = null

    // Page-owned control: present only while /kanban is on screen, so other
    // pages' headers are untouched. (The registry caches `when`, so we
    // register/dispose on route change instead.)
    const sync = () => {
      const onKanban = isKanbanRoute()

      if (onKanban && !disposeHeader) {
        disposeHeader = ctx.register({
          area: WORKSPACE_PAGE_HEADER_AREA,
          id: 'needs-input',
          order: 50,
          render: () => jsx(NeedsInputControl, { api })
        })
      } else if (!onKanban && disposeHeader) {
        disposeHeader()
        disposeHeader = null
        $open.set(false)
      }
    }

    ctx.register({
      area: PALETTE_AREA,
      data: {
        id: 'kanban-unblock.open',
        keywords: ['kanban', 'blocked', 'clarify', 'needs input', 'unblock', 'answer'],
        label: 'Kanban: answer cards that need input',
        run: () => {
          host.navigate('/kanban')
          // navigate() updates the hash synchronously; sync before opening.
          sync()
          $open.set(true)
        }
      },
      id: 'open'
    })

    window.addEventListener('hashchange', sync)
    sync()

    const teardown = () => {
      window.removeEventListener('hashchange', sync)

      if (disposeHeader) {
        disposeHeader()
        disposeHeader = null
      }
    }

    if (typeof ctx.onDispose === 'function') {
      ctx.onDispose(teardown)
    }
  }
}
