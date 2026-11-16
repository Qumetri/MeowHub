// Hub search (draft, 2026-11-16). How it works:
//  - one field under the title; `/` or Ctrl/⌘+K puts the cursor there, Esc clears it;
//  - typing filters the cards of every section at once; Enter opens the first result;
//  - a card matches when EVERY word of the query is found in its name, its description or its extra words
//    below. The extra words are what people actually type: the task ("download", "pdf"), a synonym, and the
//    Russian word, because half of the hub's users write in Russian;
//  - name hits come first, then extra words, then the description. Nothing leaves the browser.
import { services, type Service } from './services'

/** Extra words per card id (lower case). A card without an entry is still found by name and description. */
const WORDS: Record<string, string> = {
  video: 'video youtube download mp3 audio tiktok reddit twitter x vk rutube скачать видео ютуб музыка загрузчик',
  metube: 'video youtube download mp3 audio tiktok reddit twitter x vk rutube скачать видео ютуб музыка загрузчик',
  books: 'audiobook epub pdf listen voice tts narrate book аудиокнига книга озвучка слушать голос',
  upscale: 'photo image upscale enhance sharpen resolution ai фото улучшить увеличить картинка качество',
  pdftools: 'pdf merge split compress ocr word office convert watermark docx пдф документ объединить сжать конвертировать',
  chat: 'ai chat assistant llm qwen question news weather presentation ии чат бот вопрос нейросеть',
  nextcloud: 'files cloud drive storage share calendar contacts файлы облако диск хранилище',
  immich: 'photos gallery album backup camera фото галерея альбом снимки',
  plex: 'movies tv shows series anime watch stream фильмы сериалы аниме смотреть кино',
  jellyfin: 'movies tv shows series anime watch stream фильмы сериалы аниме смотреть кино',
  torrent: 'torrent magnet qbittorrent rutracker rutor download movies games торрент рутрекер скачать фильм игра раздача',
  matrix: 'messenger chat element calls video call private мессенджер чат звонки элемент',
  crypto: 'crypto bitcoin btc eth price alert tracker крипта биткоин курс цена',
  sensor: 'temperature humidity room esp32 sensor climate температура влажность датчик комната',
  homeassistant: 'smart home automation lights devices iot умный дом автоматизация свет',
  atlas: 'ui components design examples patterns interface дизайн компоненты интерфейс примеры',
  junction: 'life moments junction hackathon project проект хакатон',
  sinceai: 'ship design check hackathon ai project корабль проект',
  cyberducks: 'ducks games game play doodle flappy fun утки игры игра играть',
}

const norm = (s: string) => s.toLowerCase().replace(/ё/g, 'е').replace(/‑/g, '-')

export function searchServices(query: string): Service[] {
  const words = norm(query).split(/\s+/).filter(Boolean)
  if (!words.length) return []
  const scored: [number, Service][] = []
  for (const s of services) {
    const name = norm(`${s.name} ${s.short ?? ''}`)
    const extra = norm(WORDS[s.id] ?? '')
    const text = norm(s.description)
    let score = 0
    let all = true
    for (const w of words) {
      if (name.startsWith(w)) score += 4
      else if (name.includes(w)) score += 3
      else if (extra.split(' ').some((k) => k.startsWith(w))) score += 2
      else if (text.includes(w)) score += 1
      else { all = false; break }
    }
    if (all) scored.push([score + (s.status === 'live' ? 0.5 : 0), s])
  }
  return scored.sort((a, b) => b[0] - a[0]).map(([, s]) => s)
}
