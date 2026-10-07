import React from 'react'
import { createRoot } from 'react-dom/client'
import { setLang } from './i18n.js'
import { isTg, startTg, tgUser } from './tg.js'
import App from './App.jsx'

setLang(isTg ? tgUser?.language_code : 'ru')
startTg()
createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>)
