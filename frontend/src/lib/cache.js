/** In-memory GET cache with TTL. Keys never include secrets — only path + token fingerprint. */

const store = new Map();

/** Fingerprint a token for use in a cache key. The whole token is hashed, never
 *  sliced: the key used its first 12 characters, which is "<handle>:<part of
 *  secret>", so one user could be served another user's cached channels and a
 *  rotated token could be served the retired one's. A non-reversible digest of
 *  the full token collides only by accident. */
export function tokenFingerprint(token) {
  const text = String(token || "");
  if (!text) return "anon";
  let h = 0x811c9dc5; // FNV-1a
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `${h.toString(36)}:${text.length.toString(36)}`;
}

export function cacheKey(path, token = "") {
  return `${path}::${tokenFingerprint(token)}`;
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
