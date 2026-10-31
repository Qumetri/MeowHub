import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { api, type ApiError } from './api'
import { isTg, mainButton, pushBack } from './tg'

// GET helper: keeps stale data while refetching, so lists don't flash skeletons.
export interface ApiState<T> { data: T | null; error: ApiError | null; loading: boolean }

export function useApi<T>(path: string | null, deps: unknown[] = []) {
  const [state, setState] = useState<ApiState<T>>({ data: null, error: null, loading: !!path })
  const seq = useRef(0)
  const load = useCallback(async () => {
    if (!path) return
    const n = ++seq.current
    setState((s) => ({ ...s, error: null, loading: true }))
    try {
      const data = await api<T>(path)
      if (n === seq.current) setState({ data, error: null, loading: false })
    } catch (error) {
      if (n === seq.current) setState((s) => ({ data: s.data, error: error as ApiError, loading: false }))
    }
  }, [path])
  useEffect(() => { void load() }, [load, ...deps]) // eslint-disable-line react-hooks/exhaustive-deps
  const setData = useCallback((data: T | null) => setState((s) => ({ ...s, data })), [])
  return { ...state, reload: load, setData }
}

export function useBack(handler: (() => void) | undefined, active = true): void {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => {
    if (!active) return undefined
    return pushBack(() => ref.current?.())
  }, [active])
}

interface MainButtonOpts { text: string; enabled?: boolean; loading?: boolean; visible?: boolean; onClick: () => void }
export function useMainButton({ text, enabled = true, loading = false, visible = true, onClick }: MainButtonOpts): void {
  const ref = useRef(onClick)
  ref.current = onClick
  useEffect(() => {
    if (!isTg || !visible) return undefined
    return mainButton({ text, enabled: enabled && !loading, loading, onClick: () => ref.current?.() })
  }, [text, enabled, loading, visible])
}

export function useMedia(query: string): boolean {
  const [m, setM] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const f = () => setM(mq.matches)
    mq.addEventListener('change', f)
    return () => mq.removeEventListener('change', f)
  }, [query])
  return m
}

export function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    if (!ref.current) return undefined
    setW(ref.current.clientWidth)
    const ro = new ResizeObserver(([e]) => { if (e) setW(Math.round(e.contentRect.width)) })
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [ref])
  return w
}
