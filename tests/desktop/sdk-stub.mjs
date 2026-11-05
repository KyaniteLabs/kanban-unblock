// Minimal stand-in for @hermes/plugin-sdk: same export names the plugin
// imports, recording every host/query side effect for assertions.
import { createElement, useSyncExternalStore } from 'react'

export const calls = { invalidated: [], notified: [], navigated: [] }
export const queryState = { data: undefined, error: null, isLoading: false }

export function atom(initial) {
  let value = initial
  const listeners = new Set()

  return {
    get: () => value,
    set: next => {
      value = next
      listeners.forEach(l => l())
    },
    listen: fn => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    }
  }
}

export function useValue(a) {
  return useSyncExternalStore(a.listen, a.get, a.get)
}

export function useQuery(opts) {
  queryState.lastKey = opts.queryKey
  return { ...queryState }
}

export const queryClient = {
  invalidateQueries: ({ queryKey }) => {
    calls.invalidated.push(queryKey)
    return Promise.resolve()
  }
}

export const host = {
  navigate: to => {
    calls.navigated.push(to)
    window.location.hash = `#${to}`
  },
  notify: n => calls.notified.push(n)
}

export const cn = (...xs) => xs.filter(Boolean).join(' ')
export const WORKSPACE_PAGE_HEADER_AREA = 'workspace.pageHeader'
export const PALETTE_AREA = 'palette'

const passthrough = tag => ({ asChild, children, size, variant, align, open, onOpenChange, ...rest }) =>
  createElement(tag, rest, children)

export const Badge = passthrough('span')
export const Button = passthrough('button')
export const Input = passthrough('input')
export const Textarea = passthrough('textarea')
export const Popover = passthrough('div')
export const PopoverTrigger = passthrough('div')
export const PopoverContent = passthrough('div')
