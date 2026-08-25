import { useCallback, useEffect, useState } from "react";
import { api, apiJson } from "../lib.js";

export default function BrowserPanel({ token, flash }) {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiJson("/api/browser/status", { token });
      setStatus(res.ok ? res.data : null);
    } catch {
      setStatus(null);
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);

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

  return (
    <div className="panel-body">
      <p className="panel-note">
        Shared headless Chromium for every Bot. Ask a Bot to <code>browser_navigate</code>,
        then snapshot, click, type, or screenshot. Optional: <code>pip install playwright
        && playwright install chromium</code>. Set <code>SWARM_BROWSER=0</code> to disable.
      </p>
      <ul className="panel-list">
        <li>Enabled: {status?.enabled ? "yes" : "no"}</li>
        <li>Session: {status?.ready ? "open" : "idle"}</li>
        <li>Engine: {status?.engine || "playwright-chromium"}</li>
        <li>URL: {status?.url || "(none)"}</li>
        {status?.error ? <li>Last error: {status.error}</li> : null}
      </ul>
      <button type="button" className="btn ghost" disabled={busy || !status?.ready} onClick={closeBrowser}>
        {busy ? "Closing…" : "Close browser"}
      </button>
    </div>
  );
}
