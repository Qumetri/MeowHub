# TypeScript front ends

Every web page in MeowHub is written in **strict TypeScript**: the hub page, the
helper bot's Mini App and admin page, the PDF tools, the crypto tracker and the
photo upscaler. The backends stay dependency-free Python on purpose (nothing to
audit, nothing pulled at build time); TypeScript is where the moving parts are —
forms, polling, uploads, Telegram's WebApp API — and where a wrong field name
used to surface only in a browser.

This page explains how the pages are set up, what the types actually guarantee,
and what tripped us up while migrating ~10 000 lines of JSX/JS in October 2026.

## At a glance

| Page | Source | Build | What the types pin down |
|---|---|---|---|
| Hub (`dashboard/`) | `src/*.tsx`, `src/services.ts` | `tsc` → `vite build` | every card's icon exists in `Icon.tsx`; the host-stats payload; the `VITE_*` build settings |
| Mini App + admin (`helper/webapp/`) | `src/**/*.tsx`, `src/types.ts` | `check-i18n` → `tsc` → `vite build` | the whole bot API (`types.ts`); the dev mock matches it; `en` has exactly the keys of `ru` |
| PDF tools (`pdftools/web/`) | `src/**/*.tsx`, `src/tools.ts` | `tsc` → `vite build` | each tool's form: a field's `type` decides which props it must carry |
| Crypto tracker (`crypto/web-src/`) | `src/app.ts`, `src/demo.ts` | `tsc` emits `crypto/web/*.js` | tracker state and API shapes; the chart library's real types |
| Photo upscaler (`upscaler/web/`) | `src/app.ts` | `tsc` emits `upscaler/app/static/app.js` | job, queue and model payloads |

All of them use **TypeScript 7** (the native compiler, `typescript@7.0.2`, pinned)
and the same compiler options:

```jsonc
"strict": true,
"noUncheckedIndexedAccess": true,   // arr[i] and map[key] may be undefined — and the code says so
"noUnusedLocals": true,
"noUnusedParameters": true,
"noFallthroughCasesInSwitch": true,
"isolatedModules": true             // bundled pages: every file transpiles on its own (Vite/esbuild)
```

## Two build shapes

**Bundled pages** (hub, Mini App, PDF tools) are Vite + React apps. Vite
transpiles TypeScript itself and never type-checks, so `npm run build` is
`tsc -p . && vite build`: a type error stops the build before anything is
written to `dist/`. `tsconfig.json` has `noEmit` — `tsc` is the checker only.
`npm run typecheck` runs the same check without building.

**Script pages** (crypto tracker, upscaler) have no bundler and are served by
stdlib Python servers from images that contain no Node at all. Their TypeScript
lives next to the app (`crypto/web-src/`, `upscaler/web/`), and `tsc` *emits*
the plain classic script the server already serves (`crypto/web/app.js`,
`upscaler/app/static/app.js`). The emitted files are committed, so:

- a fresh clone runs without Node, and `docker compose build` pulls no toolchain;
- after editing the `.ts`, run `npm run build` in that folder and commit both files —
  never edit the emitted `.js` (it says so in its first lines).

The source folders are in each image's `.dockerignore`, so `node_modules` never
reaches the build context.

## The contracts

### The bot API (`helper/webapp/src/types.ts`)

One file describes every JSON shape the Python backend sends: `Me`, `Member`,
`VpnInfo`, `DlJob`, `BooksView`, `UpscaleView`, the admin rows, the key cards.
`api<T>(path)` returns `Promise<T>`, so each screen states what it expects:

```ts
const { data } = useApi<MatrixInfo>('matrix')
const r = await api<RedeemResponse>('redeem', { method: 'POST', body: { code } })
```

`mock.ts` — the fake backend behind `npm run dev` and the owner's
"preview as member" — is typed against the same file. If a field is renamed in
`types.ts`, both the screens *and* the mock stop compiling, so the preview can
no longer drift away from what the real server sends.

### Translations (`helper/webapp/src/i18n.ts`)

```ts
export type TKey = keyof typeof ru
export const en: Record<TKey, string> = { … }
```

A key missing from `en`, or an English key with no Russian twin, is a compile
error. Keys are often built from data (`t('dl.st.' + job.status)`), so `t()`
itself takes any string; `scripts/check-i18n.mjs` still runs before every build
for what types cannot see — `{placeholder}` sets that differ between languages,
plural forms, and literal `t('…')` calls to keys that do not exist. It imports
`i18n.ts` directly: Node 22 strips the types itself.

### The PDF tool catalogue (`pdftools/web/src/tools.ts`)

`Field` is a discriminated union on `type`:

```ts
| { type: 'range'; min: number; max: number; step: number; pct?: boolean; def?: number }
| { type: 'segment'; options: Option[]; def?: string }
| { type: 'chips'; options: Option[]; def?: string[]; min?: number }
…
```

A range without `step`, or a segment without `options`, does not compile, and
`Field.tsx` gets exact types in each branch of `if (f.type === …)`. `Tool.icon`
is `IconName` — the key set of the icon map — so a typo in an icon name fails
the build instead of rendering nothing.

### Icons everywhere

Each project declares its icon map with `satisfies` and exports its key set:

```ts
const P = { shield: [...], chat: [...], … } satisfies Record<string, string[]>
export type IconName = keyof typeof P
```

Hub cards, Mini App cells, PDF tools: an icon that doesn't exist is a compile
error, not a silent empty square (or, on the hub, a silent fallback to a folder).

### Telegram's WebApp (`helper/webapp/src/tg.ts`)

The part of `window.Telegram.WebApp` the app uses is declared as `TgWebApp`
(BackButton, MainButton, haptics, `showConfirm`, `downloadFile`, clipboard…), and
the dev shell `mock-tg.ts` builds its fake object against that interface.

## Conventions for contributors

- Keep `strict` on; don't add `// @ts-ignore`. If something can't be typed
  honestly, narrow it at the boundary (`as` right where JSON enters) and type
  everything after that.
- New API fields go into `types.ts` first; the screen and the mock follow.
- New translation keys go into both dictionaries — the compiler insists.
- A `catch (e)` value is `unknown`: use `(e as Error).message`, or
  `(e as ApiError).code` where the API's error codes matter.
- CSS custom properties in `style={{ '--x': … }}` need `as CSSProperties` —
  React's style type doesn't know your variables.
- Build-time `VITE_*` settings are declared in `src/vite-env.d.ts`; without that
  `import.meta.env.VITE_X` is `any` and strict mode rejects what you do with it.

## What tripped us up

- **TypeScript 7 dropped `outFile`.** The script pages were going to compile to
  one file with `outFile`; TS 7 removed it (with `module: none/amd/system`). A
  file with no `import`/`export` is still a classic script, so `rootDir` +
  `outDir` with `module: preserve` emits exactly one plain `.js` per page.
- **`npx tsc` in a folder without TypeScript installed** downloads an unrelated
  package called `tsc` that only prints a warning. Always `npm install` first
  (or call `./node_modules/.bin/tsc`).
- **Narrowing doesn't cross closures.** `if (!data) return` narrows `data` for
  the rest of the component, but not inside a `function toggle() {…}` declared
  after it. Copy it to a `const` after the guard.
- **`noUncheckedIndexedAccess` finds real edge cases**: the first candle of an
  empty chart, the last chapter of a book with no audio yet, a `Map.get` before
  `set`. Each became an explicit fallback rather than a `!`.
- **`vite.config.ts` runs in Node, the app runs in a browser.** One tsconfig with
  both `node` and `DOM` types makes `setTimeout` return `NodeJS.Timeout` in the
  page. The hub has a second `tsconfig.node.json` for the config file only.
- **pdf.js 6 removed `isEvalSupported`.** The typed options rejected it: the JS
  had been passing a setting the library silently ignored. (Eval stays blocked by
  the page's CSP, which has no `unsafe-eval`.)
- **A latent audiobook bug**: a book whose speed was never set divided its
  duration estimate by `null`. The types made the `null` visible; it now counts
  as 1.0.

## Commands

```bash
cd dashboard        && npm run typecheck   # or npm run build
cd helper/webapp    && npm run typecheck
cd pdftools/web     && npm run typecheck
cd crypto/web-src   && npm run build       # then commit web/app.js + web/demo.js
cd upscaler/web     && npm run build       # then commit app/static/app.js
```

`bootstrap.sh` runs `npm install && npm run build` for the bundled pages, so the
type check is part of every first deployment. Node.js 20.19+ or 22.12+ is needed
(Vite 8); the emitted script pages need no Node at all.
