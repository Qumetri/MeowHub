import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from './api.js'
import { isTg, mainButton, pushBack } from './tg.js'

// GET helper: keeps stale data while refetching, so lists don't flash skeletons.
export function useApi(path, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: !!path })
  const seq = useRef(0)
  const load = useCallback(async () => {
    if (!path) return
    const n = ++seq.current
    setState((s) => ({ ...s, error: null, loading: true }))
    try {
      const data = await api(path)
      if (n === seq.current) setState({ data, error: null, loading: false })
    } catch (error) {
      if (n === seq.current) setState((s) => ({ data: s.data, error, loading: false }))
    }
  }, [path])
  useEffect(() => { load() }, [load, ...deps]) // eslint-disable-line react-hooks/exhaustive-deps
  const setData = useCallback((data) => setState((s) => ({ ...s, data })), [])
  return { ...state, reload: load, setData }
}

export function useBack(handler, active = true) {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => {
    if (!active) return undefined
    return pushBack(() => ref.current?.())
  }, [active])
}

export function useMainButton({ text, enabled = true, loading = false, visible = true, onClick }) {
  const ref = useRef(onClick)
  ref.current = onClick
  useEffect(() => {
    if (!isTg || !visible) return undefined
    return mainButton({ text, enabled: enabled && !loading, loading, onClick: () => ref.current?.() })
  }, [text, enabled, loading, visible])
}

export function useMedia(query) {
  const [m, setM] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const f = () => setM(mq.matches)
    mq.addEventListener('change', f)
    return () => mq.removeEventListener('change', f)
  }, [query])
  return m
}

export function useWidth(ref) {
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    if (!ref.current) return undefined
    setW(ref.current.clientWidth)
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [ref])
  return w
}
