import { useCallback, useEffect, useState } from "react";
import { DEFAULT_MODEL, api, apiJson, bustCache, CACHE_TTL } from "../lib.js";

export default function ProviderPanel({ token, onStatusChange, flash }) {
  const [providers, setProviders] = useState([]);
  const [busy, setBusy] = useState(null);
  const [drafts, setDrafts] = useState({});

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiJson("/api/ai-support/providers", { token, cacheTtl: CACHE_TTL.catalog });
      setProviders(res.ok ? res.data : []);
    } catch {
      setProviders([]);
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  async function connect(provider) {
    const key = (drafts[provider.id]?.key || "").trim();
    if (key.length < 8) {
      flash?.("Enter a valid API key (8+ chars)", true);
      return;
    }
    setBusy(provider.id);
    try {
      const res = await api(`/api/ai-support/connect/${provider.id}`, {
        token,
        method: "POST",
        body: {
          api_key: key,
          model: drafts[provider.id]?.model || provider.default_model || undefined,
        },
      });
      if (res.ok) {
        flash?.(`Connected ${provider.name}`);
        setDrafts((d) => ({ ...d, [provider.id]: { key: "", model: d[provider.id]?.model } }));
        bustCache("/api/status", "/api/ai-support");
        await load();
        onStatusChange?.();
      } else flash?.(`Couldn't connect ${provider.name}`, true);
    } catch {
      flash?.(`Couldn't connect ${provider.name}`, true);
    }
    setBusy(null);
  }

  async function disconnect(providerId, name) {
    setBusy(providerId);
    try {
      const res = await api(`/api/ai-support/connect/${providerId}`, { token, method: "DELETE", json: false });
      if (res.ok) {
        flash?.(`Disconnected ${name}`);
        bustCache("/api/status", "/api/ai-support");
        await load();
        onStatusChange?.();
      } else flash?.("Couldn't disconnect", true);
    } catch {
      flash?.("Couldn't disconnect", true);
    }
    setBusy(null);
  }

  return (
    <div className="ai-support-panel">
      <p className="panel-note">
        Connect AI providers here — keys are stored encrypted in SQLite. Env vars still work as fallbacks.
      </p>
      <ul className="provider-list">
        {!providers.length && <li className="empty-state">Loading providers…</li>}
        {providers.map((p) => (
          <li key={p.id} className={`provider-card${p.connected ? " connected" : ""}`}>
            <div className="provider-head">
              <span className="provider-name">{p.name}</span>
              <span className="provider-kind">{p.kind || "openai_compatible"}</span>
              <span className={`provider-badge${p.connected ? " on" : ""}`}>
                {p.connected ? "Connected" : "Not connected"}
              </span>
            </div>
            {p.note && <p className="provider-desc">{p.note}</p>}
            {!p.connected ? (
              <>
                <label className="sr-only" htmlFor={`key-${p.id}`}>{p.name} API key</label>
                <input
                  id={`key-${p.id}`}
                  type="password"
                  autoComplete="off"
                  placeholder="Paste API key"
                  value={drafts[p.id]?.key || ""}
                  onChange={(e) => setDrafts((d) => ({
                    ...d,
                    [p.id]: { ...d[p.id], key: e.target.value },
                  }))}
                />
                {(p.models?.length > 0) && (
                  <select
                    value={drafts[p.id]?.model || p.default_model || p.models[0]}
                    onChange={(e) => setDrafts((d) => ({
                      ...d,
                      [p.id]: { ...d[p.id], model: e.target.value },
                    }))}
                  >
                    {p.models.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                )}
                <div className="provider-actions">
                  <a className="btn ghost" href={p.key_url} target="_blank" rel="noreferrer">Get key</a>
                  <button
                    type="button"
                    className="btn primary"
                    disabled={busy === p.id}
                    onClick={() => connect(p)}
                  >
                    {busy === p.id ? "Connecting…" : (p.oauth_label || "Connect")}
                  </button>
                </div>
              </>
            ) : (
              <div className="provider-actions">
                <span className="hint">Model: {p.model || p.default_model || "default"}</span>
                <button
                  type="button"
                  className="btn ghost"
                  disabled={busy === p.id}
                  onClick={() => disconnect(p.id, p.name)}
                >
                  Disconnect
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
