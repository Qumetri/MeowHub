import { setLang, useLang, type Lang } from '../lib/i18n'

const LANGS: [Lang, string][] = [['ru', 'Русский'], ['en', 'English']]

// RU | EN in the page's top corner; the choice is remembered per browser.
export default function LangSwitch() {
  const lang = useLang()
  return (
    <div className="lang" role="group" aria-label="Язык / Language">
      {LANGS.map(([l, name]) => (
        <button key={l} type="button" lang={l} className={l === lang ? 'on' : ''} aria-pressed={l === lang} aria-label={name} onClick={() => setLang(l)}>
          {l.toUpperCase()}
        </button>
      ))}
    </div>
  )
}
