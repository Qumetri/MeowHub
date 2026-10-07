// Dev mock only (never imported by the production build). No imports on purpose:
// mock-tg.js must be able to load before tg.js is evaluated.
export const MOCK_USERS = {
  owner: { id: 100200300, first_name: 'Админ', last_name: '', username: 'owner' },
  member: { id: 100200301, first_name: 'Алексей', last_name: 'Громов', username: 'agromov' },
  expired: { id: 100200303, first_name: 'Дмитрий', last_name: 'Орлов', username: 'dorlov' },
  stranger: { id: 100200399, first_name: 'Ирина', last_name: 'Крылова', username: 'ikrylova' },
}
