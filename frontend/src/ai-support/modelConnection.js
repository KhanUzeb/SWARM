/** Pure helpers for the model-connection surface (ModelPicker + ProviderPanel).
 *
 *  Extracted from the components so the invariants that make "connecting a
 *  model" honest are unit-testable offline: the key the picker previews with
 *  is the key the panel connects with, a save is only claimed after it
 *  succeeds, and no copy names a surface that no longer exists.
 *
 *  No React here on purpose — AGENTS.md: frontends render state, so the rules
 *  below are the one place the decision is made, and every caller follows it. */

/** The backend refuses a key shorter than this for a live preview
 *  (backend/ai_support/catalog.py `_auth_from_key`, `len(key) < 8`). Mirroring
 *  it here means the key that previews is provably a key that can connect. */
export const MIN_API_KEY_LEN = 8;

/** A key long enough (after trimming) to trade for a live model list. The one
 *  predicate shared by the picker's preview gate and ProviderPanel's connect
 *  guards, so the two can never disagree about whether a key is usable. */
export function isUsableKey(key) {
  return String(key ?? "").trim().length >= MIN_API_KEY_LEN;
}

/** The exact request the picker makes to list a provider's models.
 *
 *  A usable key previews the live API by POSTing the typed key — the key is
 *  echoed to the backend to fetch models and is never saved. Without a usable
 *  key the list comes from the stored/env credential resolved server-side, via
 *  GET. One builder so preview, catalog, and connected-list all follow it.
 *
 *  @param {{providerId?: string, apiKey?: string}} target
 * @param {(s: string) => string} [encode] injectable for tests
 */
export function modelListRequest({ providerId = "", apiKey = "" } = {}, encode = encodeURIComponent) {
  const pid = String(providerId || "");
  const key = String(apiKey || "").trim();
  if (pid && isUsableKey(key)) {
    return {
      method: "POST",
      path: `/api/ai-support/providers/${encode(pid)}/models`,
      body: { api_key: key },
    };
  }
  const path = pid ? `/api/v2/providers/${encode(pid)}/models` : "/api/v2/models/connected";
  return { method: "GET", path };
}

/** The draft model after a save attempt settles. A save is honest only once it
 *  has succeeded: on failure the draft reverts to whatever the provider is
 *  already using, so the UI never leaves an unsaved model looking active and
 *  never yanks a working model out on a transient error.
 *
 * @param {{requested: string, ok: boolean, saved?: string, serverModel?: string}} args
 */
export function draftAfterSave({ requested, ok, saved, serverModel = "" }) {
  if (ok) return { model: String(saved ?? requested ?? "") };
  return { model: String(serverModel || "") };
}

/** Empty-state copy for an empty list. Never names a route that no longer
 *  exists — the "Command Center → AI providers" path the string used to point
 *  at was retired when providers moved to the Connect destination. A backend
 *  note (why the list is empty) wins; a keyless custom server is told to type
 *  its exact id rather than promised a list it can't produce.
 *
 * @param {{note?: string, customKeyless?: boolean}} args
 */
export function modelEmptyState({ note = "", customKeyless = false } = {}) {
  const clean = String(note || "").trim();
  if (clean) return clean;
  if (customKeyless) {
    return "This server's models can't be listed without a key — type the exact model id it serves.";
  }
  return "No models found. Open Connect to connect a provider, then pick a model.";
}

/** Footer hint for the open picker. Attributes live results correctly and,
 *  crucially, never blames a missing key when a key is present but the
 *  provider didn't answer — that scenario is an unreachable provider showing
 *  catalog defaults, not a missing credential.
 *
 * @param {{error?: string, note?: string, live?: boolean, previewing?: boolean}} args
 */
export function modelFootHint({ error = "", note = "", live = false, previewing = false } = {}) {
  const cleanError = String(error || "").trim();
  if (cleanError) return cleanError;
  const cleanNote = String(note || "").trim();
  if (cleanNote) return cleanNote;
  if (live) return previewing ? "Fetched from your provider" : "Fetched from provider API";
  return previewing
    ? "Couldn't reach the provider — showing catalog defaults"
    : "Catalog models — connect a key for live results";
}
