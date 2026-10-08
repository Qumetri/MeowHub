// Dev mock only: a fake Telegram.WebApp so the native look can be checked in a
// plain browser (?mock=member&mode=tg&theme=dark). Never shipped: main.jsx
// reaches this file only behind import.meta.env.DEV.
import { MOCK_USERS } from './mock-users.js'

const THEMES = {
  light: {
    bg_color: '#ffffff', text_color: '#000000', hint_color: '#8e8e93', link_color: '#2481cc',
    button_color: '#3390ec', button_text_color: '#ffffff', secondary_bg_color: '#efeff4',
    header_bg_color: '#ffffff', bottom_bar_bg_color: '#ffffff', accent_text_color: '#2481cc',
    section_bg_color: '#ffffff', section_header_text_color: '#6d6d72', section_separator_color: '#d8d8dc',
    subtitle_text_color: '#7e7e83', destructive_text_color: '#e53935',
  },
  dark: {
    bg_color: '#1c1c1d', text_color: '#ffffff', hint_color: '#98989e', link_color: '#6ab3f3',
    button_color: '#3e88f7', button_text_color: '#ffffff', secondary_bg_color: '#0a0a0a',
    header_bg_color: '#1c1c1d', bottom_bar_bg_color: '#1c1c1d', accent_text_color: '#6ab3f3',
    section_bg_color: '#1c1c1d', section_header_text_color: '#8d8e93', section_separator_color: '#38383a',
    subtitle_text_color: '#98989e', destructive_text_color: '#ef5b5b',
  },
}

export function installFakeTelegram(p) {
  const scheme = p.get('theme') === 'dark' ? 'dark' : 'light'
  const theme = THEMES[scheme]
  const root = document.documentElement
  for (const [k, v] of Object.entries(theme)) root.style.setProperty('--tg-theme-' + k.replace(/_/g, '-'), v)
  root.style.setProperty('--tg-safe-area-inset-bottom', '0px')

  const mock = p.get('mock')
  const user = { ...(MOCK_USERS[mock] || MOCK_USERS.member), language_code: p.get('lc') || (['ru', 'en'].includes(p.get('lang')) ? p.get('lang') : 'ru') }
  const user_json = JSON.stringify(user)

  // --- fake native chrome: header on top, bottom bar for the MainButton
  const css = document.createElement('style')
  css.textContent = `
    body{display:flex;flex-direction:column;min-height:100vh}
    #root{flex:1}
    .app{min-height:calc(100vh - 46px) !important}
    #ftg-head{position:sticky;top:0;z-index:99;height:46px;flex:none;display:flex;align-items:center;justify-content:space-between;padding:0 14px;
      background:${theme.header_bg_color};color:${theme.text_color};font:600 16px -apple-system,system-ui,sans-serif;border-bottom:.5px solid ${theme.section_separator_color}}
    #ftg-head button{color:${theme.link_color};font:400 17px -apple-system,system-ui,sans-serif;min-width:80px;text-align:left;background:none;border:0}
    #ftg-head span{color:${theme.hint_color};min-width:80px;text-align:right;font-weight:400}
    #ftg-main{position:sticky;bottom:0;z-index:99;flex:none;padding:10px 16px calc(10px);background:${theme.bottom_bar_bg_color};border-top:.5px solid ${theme.section_separator_color};display:none}
    #ftg-main button{width:100%;height:48px;border:0;border-radius:12px;background:${theme.button_color};color:${theme.button_text_color};font:600 17px -apple-system,system-ui,sans-serif}
    #ftg-main button:disabled{opacity:.45}
    #ftg-flash{position:fixed;left:8px;right:8px;top:54px;z-index:200;background:#222;color:#fff;font:12px ui-monospace,monospace;padding:6px 10px;border-radius:8px;display:none;word-break:break-all}
    #ftg-dlg{position:fixed;inset:0;z-index:300;background:rgba(0,0,0,.4);display:none;align-items:center;justify-content:center}
    #ftg-dlg div{width:270px;background:${theme.bg_color};color:${theme.text_color};border-radius:14px;overflow:hidden;font:15px -apple-system,system-ui,sans-serif;text-align:center}
    #ftg-dlg p{padding:20px 16px;line-height:1.35}
    #ftg-dlg footer{display:flex;border-top:.5px solid ${theme.section_separator_color}}
    #ftg-dlg footer button{flex:1;height:44px;border:0;background:none;color:${theme.link_color};font:inherit;font-size:17px}
    #ftg-dlg footer button+button{border-left:.5px solid ${theme.section_separator_color};font-weight:600}`
  document.head.appendChild(css)

  const head = document.createElement('div')
  head.id = 'ftg-head'
  const ru = String(user.language_code || '').startsWith('ru')
  const L = ru ? { close: 'Закрыть', back: '‹ Назад' } : { close: 'Close', back: '‹ Back' }
  head.innerHTML = '<button id="ftg-back">' + L.close + '</button>MeowHub<span>⋯</span>'
  document.body.insertBefore(head, document.getElementById('root'))
  const mainBar = document.createElement('div')
  mainBar.id = 'ftg-main'
  mainBar.innerHTML = '<button></button>'
  document.body.appendChild(mainBar)
  const flash = document.createElement('div'); flash.id = 'ftg-flash'; document.body.appendChild(flash)
  const dlg = document.createElement('div'); dlg.id = 'ftg-dlg'
  dlg.innerHTML = '<div><p></p><footer><button data-v="0">Отмена</button><button data-v="1">OK</button></footer></div>'
  document.body.appendChild(dlg)
  const say = (m) => { flash.textContent = m; flash.style.display = 'block'; console.info('[fake tg]', m); setTimeout(() => { flash.style.display = 'none' }, 2500) }

  const backBtn = head.querySelector('#ftg-back')
  let backVisible = false
  const backHandlers = new Set()
  backBtn.onclick = () => { if (backVisible) backHandlers.forEach((f) => f()); else say('WebApp.close()') }
  const BackButton = {
    get isVisible() { return backVisible },
    show() { backVisible = true; backBtn.textContent = L.back },
    hide() { backVisible = false; backBtn.textContent = L.close },
    onClick(f) { backHandlers.add(f) }, offClick(f) { backHandlers.delete(f) },
  }

  const btn = mainBar.querySelector('button')
  const mainHandlers = new Set()
  btn.onclick = () => mainHandlers.forEach((f) => f())
  const MainButton = {
    isVisible: false,
    setParams(o) {
      if (o.text != null) btn.textContent = o.text
      if (o.is_active != null) btn.disabled = !o.is_active
      if (o.is_visible != null) { mainBar.style.display = o.is_visible ? 'block' : 'none'; this.isVisible = o.is_visible }
    },
    show() { this.setParams({ is_visible: true }) }, hide() { this.setParams({ is_visible: false }) },
    showProgress() { btn.dataset.busy = 1; btn.style.opacity = 0.6 }, hideProgress() { btn.style.opacity = ''; delete btn.dataset.busy },
    onClick(f) { mainHandlers.add(f) }, offClick(f) { mainHandlers.delete(f) },
  }

  const haptic = (k) => (...a) => console.info('[fake tg] haptic', k, ...a)
  window.Telegram = {
    WebApp: {
      initData: 'query_id=MOCK&user=' + encodeURIComponent(user_json) + '&auth_date=1&hash=mock',
      initDataUnsafe: { user },
      colorScheme: scheme, themeParams: theme, version: '8.0', platform: 'ios',
      ready() {}, expand() {}, close() { say('WebApp.close()') },
      setHeaderColor() {}, setBackgroundColor() {}, onEvent() {}, offEvent() {},
      BackButton, MainButton,
      HapticFeedback: { impactOccurred: haptic('impact'), notificationOccurred: haptic('notify'), selectionChanged: haptic('select') },
      showConfirm(msg, cb) {
        dlg.querySelector('p').textContent = msg
        dlg.style.display = 'flex'
        dlg.querySelectorAll('button').forEach((b) => {
          b.onclick = () => { dlg.style.display = 'none'; cb(b.dataset.v === '1') }
        })
      },
      isVersionAtLeast: () => true,
      downloadFile(o, cb) { say('downloadFile ' + o.file_name + ' <- ' + o.url); cb?.(true) },
      openLink(u) { say('openLink ' + u) },
      openTelegramLink(u) { say('openTelegramLink ' + u) },
    },
  }
}
