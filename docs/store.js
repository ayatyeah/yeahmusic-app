// Мои песни: хранятся прямо в браузере телефона (IndexedDB) — и сама песня, и разметка.
// Никуда не уходят: это файлы на твоём устройстве, просто приложение их помнит.

const DB = 'yeahmusic';
const STORE = 'tracks';

function open() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE, { keyPath: 'id' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function run(mode, action) {
  return open().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const req = action(tx.objectStore(STORE));
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  }));
}

export const store = {
  /** Все песни, новые сверху. */
  async list() {
    const all = await run('readonly', (s) => s.getAll());
    return all.sort((a, b) => b.added - a.added);
  },

  get(id) { return run('readonly', (s) => s.get(id)); },

  /** Сохранить песню (audio — File/Blob, data — разметка или null). */
  async save({ id, name, audio, data }) {
    const item = { id: id || `t${Date.now()}`, name, audio, data, added: Date.now() };
    await run('readwrite', (s) => s.put(item));
    return item;
  },

  remove(id) { return run('readwrite', (s) => s.delete(id)); },

  /** Сколько места занято песнями (МБ) — чтобы видеть, что хранится на телефоне. */
  async size() {
    const all = await run('readonly', (s) => s.getAll());
    const bytes = all.reduce((sum, t) => sum + (t.audio?.size || 0), 0);
    return Math.round(bytes / 1048576 * 10) / 10;
  },
};
