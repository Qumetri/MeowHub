import { createContext, useContext } from 'react'
import type { AppContext } from './types'

export const AppCtx = createContext<AppContext | null>(null)
export function useApp(): AppContext {
  const ctx = useContext(AppCtx)
  if (!ctx) throw new Error('useApp() outside <AppCtx.Provider>')
  return ctx
}
