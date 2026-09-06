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
export function useWorkSessions(token, { pollMs = 4000 } = {}) {
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
      // Replay any missed events for active sessions.
      for (const s of list.filter(x => WORK_ACTIVE.has(x.status))) {
        try {
          const after = cursors.current.get(s.id) || 0;
          const incoming = await fetchWorkEvents(tokenRef.current, s.id, after);
          if (incoming.length) {
            cursors.current.set(s.id, Math.max(after, ...incoming.map(e => e.seq)));
            setEventsByWork(prev => ({ ...prev, [s.id]: mergeWorkEvents(prev[s.id], incoming) }));
          }
        } catch { /* per-session replay must not fail the whole refresh */ }
      }
    } catch (e) {
      setConnected(false);
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    if (!token) return;
    refresh();
    const timer = setInterval(refresh, pollMs);
    return () => clearInterval(timer);
  }, [token, pollMs, refresh]);

  return { sessions, eventsByWork, connected, error, refresh, ingestWorkEvent };
}
