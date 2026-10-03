import { useCallback, useEffect, useRef, useState } from "react";
import { apiJson } from "../ui.jsx";
import {
  WORK_ACTIVE, WORK_TERMINAL, applyWorkEventToSession,
  groupWorkByAttention, mergeWorkEvents,
} from "./workUtils.js";

export { WORK_ACTIVE, WORK_TERMINAL, applyWorkEventToSession, groupWorkByAttention, mergeWorkEvents };
export { workStatusLabel, workStatusTone } from "./workUtils.js";

export async function fetchWork(token, limit = 50) {
  const res = await apiJson(`/api/work?limit=${limit}`, { token });
  if (!res.ok) throw new Error(res.data?.detail || "work list failed");
  return res.data;
}

export async function fetchWorkEvents(token, workId, after = 0) {
  const res = await apiJson(`/api/work/${encodeURIComponent(workId)}/events?after=${after}`, { token });
  if (!res.ok) throw new Error(res.data?.detail || "work events failed");
  return res.data;
}

export async function fetchWorkMessages(token, workId) {
  const res = await apiJson(`/api/work/${encodeURIComponent(workId)}/messages`, { token });
  if (!res.ok) throw new Error(res.data?.detail || "work messages failed");
  return res.data;
}

export async function cancelWork(token, workId) {
  const res = await apiJson(`/api/work/${encodeURIComponent(workId)}/cancel`, { token, method: "POST" });
  if (!res.ok) throw new Error(res.data?.detail || "cancel failed");
  return res.data;
}

/**
 * useWorkSessions — polls the normalized work interface and merges live
 * `{"type":"work","event"}` frames arriving on the existing chat socket.
 * Event cursors are monotonic per work_id and replay-safe after reconnects.
 */
export function useWorkSessions(token, { pollMs = 4000, healthyMs = 30000 } = {}) {
  const [sessions, setSessions] = useState([]);
  const [eventsByWork, setEventsByWork] = useState({});
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);
  const cursors = useRef(new Map());
  const tokenRef = useRef(token);
  tokenRef.current = token;

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
    if (!tokenRef.current) return;
    try {
      const list = await fetchWork(tokenRef.current);
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
          const incoming = await fetchWorkEvents(tokenRef.current, s.id, after);
          if (incoming.length) {
            cursors.current.set(s.id, Math.max(after, ...incoming.map(e => e.seq)));
            setEventsByWork(prev => ({ ...prev, [s.id]: mergeWorkEvents(prev[s.id], incoming) }));
          } else if (!cursors.current.has(s.id)) {
            cursors.current.set(s.id, after); // fetched once; no events to replay
          }
        } catch { /* per-session replay must not fail the whole refresh */ }
      }
    } catch (e) {
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
    if (!token) return;
    refresh();
    let timer = setInterval(refresh, pollMs);
    const settle = setInterval(() => {
      const next = connectedRef.current ? healthyMs : pollMs;
      clearInterval(timer);
      timer = setInterval(refresh, next);
    }, 2000);
    return () => {
      clearInterval(timer);
      clearInterval(settle);
    };
  }, [token, pollMs, healthyMs, refresh]);

  return { sessions, eventsByWork, connected, error, refresh, ingestWorkEvent };
}
