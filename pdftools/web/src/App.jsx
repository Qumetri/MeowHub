import { useCallback, useEffect, useState } from 'react'
import { TOOL_BY_ID } from './tools.js'
import Home from './components/Home.jsx'
import ToolView from './components/ToolView.jsx'

// Hash routes keep every tool linkable (#/compress) under any secret path, with no server routes.
function readRoute() {
  const m = /^#\/([\w-]+)/.exec(window.location.hash)
  return m && TOOL_BY_ID[m[1]] ? m[1] : null
}

export default function App() {
  const [toolId, setToolId] = useState(readRoute)
  // Files handed from one tool's result to the next ("continue with this file").
  const [carry, setCarry] = useState(null)

  useEffect(() => {
    const onHash = () => setToolId(readRoute())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    document.title = toolId ? `${TOOL_BY_ID[toolId].title} — Канцелярия` : 'Канцелярия'
    window.scrollTo(0, 0)
  }, [toolId])

  const open = useCallback((id, files = null) => {
    setCarry(files)
    window.location.hash = id ? `#/${id}` : ''
    if (!id) history.replaceState(null, '', window.location.pathname + window.location.search)
    setToolId(id)
  }, [])

  return toolId
    ? <ToolView key={toolId} tool={TOOL_BY_ID[toolId]} initialFiles={carry} onOpen={open} />
    : <Home onOpen={open} />
}
