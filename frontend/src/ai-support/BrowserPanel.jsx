import { useCallback, useEffect, useState } from "react";
import { api, apiJson } from "../lib.js";

export default function BrowserPanel({ token, flash }) {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiJson("/api/browser/status", { token, cacheTtl: 0 });
      setStatus(res.ok ? res.data : null);
    } catch {
      setStatus(null);
    }
  }, [token]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  async function closeBrowser() {
    setBusy(true);
    try {
      const res = await api("/api/browser/close", { token, method: "POST", body: {} });
      flash?.(res.ok ? "Browser closed" : "Couldn't close the browser", !res.ok);
      await load();
    } catch {
      flash?.("Couldn't close the browser", true);
    }
    setBusy(false);
  }

  const enabled = !!status?.enabled;
  const ready = !!status?.ready;

  return (
    <div className="panel-body browser-panel">
      <p className="panel-note">
        Shared headless Chromium for every bot. Ask a bot to <code>browser_navigate</code>, then snapshot, click, type, or screenshot.
      </p>
      <div className="status-grid">
        <div className={`status-card${enabled ? " on" : ""}`}>
          <span className="status-card-label">Engine</span>
          <strong>{status?.engine || "playwright-chromium"}</strong>
          <span className="status-card-sub">{enabled ? "Enabled" : "Disabled"}</span>
        </div>
        <div className={`status-card${ready ? " on" : ""}`}>
          <span className="status-card-label">Session</span>
          <strong>{ready ? "Open" : "Idle"}</strong>
          <span className="status-card-sub">{ready ? "Active tab" : "No page loaded"}</span>
        </div>
      </div>
      {status?.url && (
        <div className="browser-url">
          <span className="field-label">Current URL</span>
          <code>{status.url}</code>
        </div>
      )}
      {status?.error && <p className="panel-error">{status.error}</p>}
      {!enabled && (
        <p className="panel-note">
          Install with <code>pip install playwright && playwright install chromium</code>. Set <code>SWARM_BROWSER=0</code> to disable.
        </p>
      )}
      <div className="panel-actions">
        <button type="button" className="btn ghost" onClick={load}>Refresh status</button>
        <button type="button" className="btn ghost" disabled={busy || !ready} onClick={closeBrowser}>
          {busy ? "Closing…" : "Close browser"}
        </button>
      </div>
    </div>
  );
}
