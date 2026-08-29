/** In-memory GET cache with TTL. Keys never include secrets — only path + token fingerprint. */

const store = new Map();

export function cacheKey(path, token = "") {
  const fp = token ? token.slice(0, 12) : "anon";
  return `${path}::${fp}`;
}

export function cacheGet(key) {
  const row = store.get(key);
  if (!row) return undefined;
  if (row.expires <= Date.now()) {
    store.delete(key);
    return undefined;
  }
  return row.data;
}

export function cacheSet(key, data, ttlMs) {
  if (!ttlMs || ttlMs <= 0) return;
  store.set(key, { data, expires: Date.now() + ttlMs });
}

export function cacheInvalidate(prefix = "") {
  if (!prefix) {
    store.clear();
    return;
  }
  for (const key of store.keys()) {
    if (key.startsWith(prefix)) store.delete(key);
  }
}
