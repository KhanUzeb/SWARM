import { useCallback, useEffect, useState } from "react";
import { api, apiJson, bustCache } from "../lib.js";

export default function AppsPanel({ token, flash }) {
  const [connectors, setConnectors] = useState([]);
  const [keys, setKeys] = useState({});
  const [toolkit, setToolkit] = useState("");
  const [toolkits, setToolkits] = useState("");
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiJson("/api/connectors", { token });
      setConnectors(res.ok ? res.data : []);
    } catch {
      setConnectors([]);
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  async function connect(id) {
    const apiKey = (keys[id] || "").trim();
    if (apiKey.length < 8) {
      flash?.("Enter an API key (8+ chars)", true);
      return;
    }
    setBusy(`connect-${id}`);
    try {
      const res = await api(`/api/connectors/${id}/connect`, {
        token,
        method: "POST",
        body: { api_key: apiKey },
      });
      if (res.ok) {
        flash?.(`Connected ${id}`);
        setKeys((k) => ({ ...k, [id]: "" }));
        bustCache("/api/status", "/api/connectors", "/api/composio");
        await load();
      } else flash?.(`Couldn't connect ${id}`, true);
    } catch {
      flash?.(`Couldn't connect ${id}`, true);
    }
    setBusy(null);
  }

  async function disconnect(id) {
    setBusy(`disconnect-${id}`);
    try {
      const res = await api(`/api/connectors/${id}/connect`, { token, method: "DELETE", json: false });
      if (res.ok) {
        flash?.(`Disconnected ${id}`);
        bustCache("/api/status", "/api/connectors", "/api/composio");
        await load();
      } else flash?.("Couldn't disconnect", true);
    } catch {
      flash?.("Couldn't disconnect", true);
    }
    setBusy(null);
  }

  async function listToolkits() {
    setBusy("list");
    try {
      const q = toolkit.trim();
      const path = q ? `/api/composio/toolkits?q=${encodeURIComponent(q)}` : "/api/composio/toolkits";
      const res = await apiJson(path, { token });
      setToolkits(res.ok ? (res.data?.text || "") : "(couldn't list toolkits)");
    } catch {
      setToolkits("(couldn't list toolkits)");
    }
    setBusy(null);
  }

  async function connectToolkit() {
    const slug = toolkit.trim();
    if (!slug) {
      flash?.("Enter a toolkit slug such as gmail or github", true);
      return;
    }
    setBusy("toolkit");
    try {
      const res = await api("/api/composio/connect-toolkit", {
        token,
        method: "POST",
        body: { toolkit: slug },
      });
      const data = res.ok ? await res.json() : null;
      setToolkits(data?.text || "(couldn't start connect)");
      flash?.(res.ok ? "Open the URL in the result to authorize the app" : "Couldn't start connect", !res.ok);
    } catch {
      flash?.("Couldn't start connect", true);
    }
    setBusy(null);
  }

  const composio = connectors.find((c) => c.id === "composio");

  return (
    <div className="panel-body apps-panel">
      <p className="panel-note">
        Workspace connectors shared by every bot — search, scrape, apps, browser automation, and desktop drivers.
      </p>
      <ul className="connector-grid">
        {!connectors.length && <li className="empty-state">Loading connectors…</li>}
        {connectors.map((c) => (
          <li key={c.id} className={`connector-card${c.connected ? " connected" : ""}`}>
            <div className="connector-head">
              <strong className="connector-name">{c.name}</strong>
              <span className={`provider-badge${c.connected ? " on" : ""}`}>
                {c.connected ? "Ready" : "Not connected"}
              </span>
            </div>
            <p className="connector-meta">{c.kind}{c.key_hint ? ` · ${c.key_hint}` : ""}</p>
            <p className="connector-desc">{c.note}</p>
            {c.tools?.length > 0 && (
              <div className="tool-grid">
                {c.tools.map(t => <span key={t} className="tool-tag plugin">{t}</span>)}
              </div>
            )}
            {c.kind === "cli" ? (
              <p className="hint">Local install — no API key required.</p>
            ) : !c.connected ? (
              <form className="mini-form" onSubmit={(ev) => { ev.preventDefault(); connect(c.id); }}>
                <input
                  type="password"
                  autoComplete="off"
                  placeholder={c.env_fallback || "API key"}
                  value={keys[c.id] || ""}
                  onChange={(e) => setKeys((k) => ({ ...k, [c.id]: e.target.value }))}
                />
                <div className="modal-actions">
                  {c.key_url ? <a className="btn ghost" href={c.key_url} target="_blank" rel="noreferrer">Get key</a> : null}
                  <button type="submit" className="btn primary" disabled={busy === `connect-${c.id}`}>
                    {busy === `connect-${c.id}` ? "Connecting…" : "Connect"}
                  </button>
                </div>
              </form>
            ) : (
              <button type="button" className="btn ghost btn-sm" disabled={busy === `disconnect-${c.id}`} onClick={() => disconnect(c.id)}>
                Disconnect stored key
              </button>
            )}
          </li>
        ))}
      </ul>
      {composio?.connected && (
        <div className="panel-section">
          <div className="mem-title">Composio app toolkits</div>
          <form className="mini-form" onSubmit={(ev) => { ev.preventDefault(); listToolkits(); }}>
            <input
              placeholder="gmail, github, slack, notion…"
              value={toolkit}
              onChange={(e) => setToolkit(e.target.value)}
            />
            <div className="modal-actions">
              <button type="submit" className="btn primary" disabled={busy === "list"}>
                {busy === "list" ? "Listing…" : "List toolkits"}
              </button>
              <button type="button" className="btn ghost" disabled={busy === "toolkit"} onClick={connectToolkit}>
                {busy === "toolkit" ? "Starting…" : "Connect this app"}
              </button>
            </div>
          </form>
          {toolkits ? <pre id="file-preview">{toolkits}</pre> : null}
        </div>
      )}
    </div>
  );
}
