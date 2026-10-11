import { useCallback, useEffect, useRef, useState } from "react";
// lib.js owns the API client; reaching for it through the UI barrel made this
// data hook depend on every component's module for one function.
import { apiJson, isUnauthorized } from "../lib.js";
import {
  WORK_ACTIVE, WORK_TERMINAL, applyWorkEventToSession,
  groupWorkByAttention, mergeWorkEvents,
} from "./workUtils.js";

export { WORK_ACTIVE, WORK_TERMINAL, applyWorkEventToSession, groupWorkByAttention, mergeWorkEvents };
export { workStatusLabel, workStatusTone } from "./workUtils.js";

/** A failed call, carrying the HTTP status so the poller can tell "this token
 *  is dead" from "the work feed is unhappy". The status used to be dropped, and
 *  a 401 was reported to the user as "Work feed failed" with a Retry button
 *  that could never succeed. */
function failed(what, res) {
  return Object.assign(new Error(res?.data?.detail || what), { status: res?.status });
}

export async function fetchWork(token, limit = 50) {
  const res = await apiJson(`/api/work?limit=${limit}`, { token });
  if (!res.ok) throw failed("work list failed", res);
  return res.data;
}

export async function fetchWorkEvents(token, workId, after = 0) {
  const res = await apiJson(`/api/work/${encodeURIComponent(workId)}/events?after=${after}`, { token });
  if (!res.ok) throw failed("work events failed", res);
  return res.data;
}

export async function fetchWorkMessages(token, workId) {
  const res = await apiJson(`/api/work/${encodeURIComponent(workId)}/messages`, { token });
  if (!res.ok) throw failed("work messages failed", res);
  return res.data;
}

export async function cancelWork(token, workId) {
  const res = await apiJson(`/api/work/${encodeURIComponent(workId)}/cancel`, { token, method: "POST" });
  if (!res.ok) throw failed("cancel failed", res);
  return res.data;
}

/**
 * useWorkSessions — polls the normalized work interface and merges live
 * `{"type":"work","event"}` frames arriving on the existing chat socket.
 * Event cursors are monotonic per work_id and replay-safe after reconnects.
 */
export function useWorkSessions(token, { pollMs = 4000, healthyMs = 30000, onUnauthorized } = {}) {
  const [sessions, setSessions] = useState([]);
  const [eventsByWork, setEventsByWork] = useState({});
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);
  const cursors = useRef(new Map());
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const alive = useRef(true);
  const onUnauthorizedRef = useRef(onUnauthorized);
  onUnauthorizedRef.current = onUnauthorized;

  const ingestWorkEvent = useCallback((event) => {
    if (!event?.work_id || typeof event.seq !== "number") return;
    const id = event.work_id;
    const known = cursors.current.get(id) || 0;
    if (event.seq <= known) return;
    cursors.current.set(id, event.seq);
    setEventsByWork(prev => ({ ...prev, [id]: mergeWorkEvents(prev[id], [event]) }));
    setSessions(prev => prev.map(s => applyWorkEventToSession(s, event)));
  }, []);

  const refresh = useCallback(async () => {
    const t = tokenRef.current;
    if (!t) {
      // Signed out. Saying nothing here left the rail on its loading skeleton
      // for ever, because the rail shows it while it has no runs and no error.
      setSessions([]); setEventsByWork({}); cursors.current = new Map();
      setConnected(false); setError(null);
      return;
    }
    try {
      const list = await fetchWork(t);
      // The token can be revoked or replaced while the request is in the air;
      // writing that answer into the next session's rail is how one user's runs
      // showed up under another's name.
      if (!alive.current || tokenRef.current !== t) return;
      setSessions(groupWorkByAttention(list));
      setConnected(true);
      setError(null);
      // Replay any missed events for active sessions — plus terminal ones
      // we have never fetched (e.g. finished while the socket was down).
      const needsReplay = list.filter(x =>
        WORK_ACTIVE.has(x.status) || !cursors.current.has(x.id));
      for (const s of needsReplay) {
        try {
          const after = cursors.current.get(s.id) || 0;
          const incoming = await fetchWorkEvents(t, s.id, after);
          if (!alive.current || tokenRef.current !== t) return;
          if (incoming.length) {
            cursors.current.set(s.id, Math.max(after, ...incoming.map(e => e.seq)));
            setEventsByWork(prev => ({ ...prev, [s.id]: mergeWorkEvents(prev[s.id], incoming) }));
          } else if (!cursors.current.has(s.id)) {
            cursors.current.set(s.id, after); // fetched once; no events to replay
          }
        } catch { /* per-session replay must not fail the whole refresh */ }
      }
    } catch (e) {
      if (!alive.current || tokenRef.current !== t) return;
      if (isUnauthorized(e)) { onUnauthorizedRef.current?.(); return; }
      setConnected(false);
      setError(e.message);
    }
  }, []);

  // The socket already pushes work events, so polling is only a safety net for
  // a dropped connection - not the primary way data arrives. Poll fast while
  // disconnected, and back off to a slow confirmation sweep once the socket is
  // healthy, so an always-open tab is not re-fetching every few seconds all
  // day. `refresh` stays exposed for the manual retry paths that call it.
  // Read through a ref: putting `connected` in the deps would tear down and
  // rebuild the timer every time the socket state flipped, which is exactly
  // when the fast interval matters most.
  const connectedRef = useRef(connected);
  connectedRef.current = connected;

  useEffect(() => {
    // Every run, event and cursor belongs to the token that was in hand. They
    // survived a sign-out, so signing in as somebody else showed their runs in
    // the rail until the first poll answered.
    cursors.current = new Map();
    setSessions([]);
    setEventsByWork({});
    setError(null);
    setConnected(false);
    if (!token) return;
    alive.current = true;
    refresh();
    let timer = setInterval(refresh, pollMs);
    const settle = setInterval(() => {
      const next = connectedRef.current ? healthyMs : pollMs;
      clearInterval(timer);
      timer = setInterval(refresh, next);
    }, 2000);
    return () => {
      // Stop a poll that is still in the air from writing into a session that
      // has already been torn down.
      alive.current = false;
      clearInterval(timer);
      clearInterval(settle);
    };
  }, [token, pollMs, healthyMs, refresh]);

  return { sessions, eventsByWork, connected, error, refresh, ingestWorkEvent };
}
