import { useCallback, useEffect, useState } from 'react'
import { TOOL_BY_ID } from './tools'

export type OpenTool = (id: string | null, files?: File[] | null) => void
import Home from './components/Home'
import ToolView from './components/ToolView'

// Hash routes keep every tool linkable (#/compress) under any secret path, with no server routes.
function readRoute(): string | null {
  const m = /^#\/([\w-]+)/.exec(window.location.hash)
  return m?.[1] && TOOL_BY_ID[m[1]] ? m[1] : null
}

export default function App() {
  const [toolId, setToolId] = useState(readRoute)
  // Files handed from one tool's result to the next ("continue with this file").
  const [carry, setCarry] = useState<File[] | null>(null)

  useEffect(() => {
    const onHash = () => setToolId(readRoute())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    const tool = toolId ? TOOL_BY_ID[toolId] : undefined
    document.title = tool ? `${tool.title} — Канцелярия` : 'Канцелярия'
    window.scrollTo(0, 0)
  }, [toolId])

  const open = useCallback<OpenTool>((id, files = null) => {
    setCarry(files)
    window.location.hash = id ? `#/${id}` : ''
    if (!id) history.replaceState(null, '', window.location.pathname + window.location.search)
    setToolId(id)
  }, [])

  const tool = toolId ? TOOL_BY_ID[toolId] : undefined
  return tool
    ? <ToolView key={tool.id} tool={tool} initialFiles={carry} onOpen={open} />
    : <Home onOpen={open} />
}
