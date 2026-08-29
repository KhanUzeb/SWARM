import { useCallback, useEffect, useState } from "react";
import { api, apiJson, bustCache, CACHE_TTL } from "../lib.js";
import ModelPicker from "./ModelPicker.jsx";

export default function ProviderPanel({ token, onStatusChange, flash }) {
  const [providers, setProviders] = useState([]);
  const [busy, setBusy] = useState(null);
  const [drafts, setDrafts] = useState({});

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiJson("/api/v2/providers", { token, cacheTtl: CACHE_TTL.catalog });
      setProviders(res.ok ? res.data : []);
    } catch {
      setProviders([]);
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  function draftFor(provider) {
    return drafts[provider.id] || {
      key: "",
      model: provider.model || provider.default_model || "",
    };
  }

  async function connect(provider) {
    const draft = draftFor(provider);
    const key = (draft.key || "").trim();
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
          model: draft.model || provider.default_model || undefined,
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

  async function startOAuth(provider) {
    try {
      const res = await apiJson(`/api/v2/providers/${provider.id}/oauth/start`, { token });
      if (res.ok && res.data?.authorization_url) window.open(res.data.authorization_url, "swarm-provider-oauth", "popup,width=520,height=720");
      else flash?.(res.data?.detail || "OAuth is not configured for this provider", true);
    } catch { flash?.("Could not start OAuth", true); }
  }

  async function saveModel(provider) {
    const model = (draftFor(provider).model || provider.model || "").trim();
    if (!model) {
      flash?.("Pick a model first", true);
      return;
    }
    setBusy(`model-${provider.id}`);
    try {
      const res = await api(`/api/ai-support/connect/${provider.id}`, {
        token,
        method: "PATCH",
        body: { model },
      });
      if (res.ok) {
        flash?.(`Using ${model} on ${provider.name}`);
        bustCache("/api/status", "/api/ai-support");
        await load();
        onStatusChange?.();
      } else flash?.("Couldn't save that model", true);
    } catch {
      flash?.("Couldn't save that model", true);
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
        Connect a provider, then pick a live model from their API. Keys stay encrypted in SQLite.
        Env vars still work as fallbacks.
      </p>
      <ul className="provider-list">
        {!providers.length && <li className="empty-state">Loading providers…</li>}
        {providers.map((p) => {
          const draft = draftFor(p);
          return (
            <li key={p.id} className={`provider-card${p.connected ? " connected" : ""}`}>
              <div className="provider-head">
                <span className="provider-name">{p.name}</span>
                <span className="provider-kind">{p.kind || "openai_compatible"}</span>
                <span className="provider-kind">{(p.auth_methods || ["api_key"]).join(" / ")}</span>
                <span className={`provider-badge${p.connected ? " on" : ""}`}>
                  {p.connected ? (p.via === "env" ? "Env" : "Connected") : "Not connected"}
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
                    value={draft.key}
                    onChange={(e) => setDrafts((d) => ({
                      ...d,
                      [p.id]: { ...draftFor(p), key: e.target.value },
                    }))}
                  />
                  <label className="field-label" htmlFor={`model-${p.id}`}>Model</label>
                  <ModelPicker
                    id={`model-${p.id}`}
                    token={token}
                    providerId={p.id}
                    apiKey={draft.key}
                    value={draft.model || p.default_model || ""}
                    onChange={(model) => setDrafts((d) => ({
                      ...d,
                      [p.id]: { ...draftFor(p), model },
                    }))}
                    placeholder="Search this provider's models"
                  />
                  <div className="provider-actions">
                    {p.auth_methods?.includes("oauth") && p.oauth_configured && <button type="button" className="btn ghost" onClick={() => startOAuth(p)}>Connect with OAuth</button>}
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
                <>
                  <label className="field-label" htmlFor={`model-${p.id}`}>Model</label>
                  <ModelPicker
                    id={`model-${p.id}`}
                    token={token}
                    providerId={p.id}
                    value={draft.model || p.model || p.default_model || ""}
                    onChange={(model) => setDrafts((d) => ({
                      ...d,
                      [p.id]: { ...draftFor(p), model },
                    }))}
                    placeholder="Search live models"
                  />
                  <div className="provider-actions">
                    {p.via !== "env" && (
                      <button
                        type="button"
                        className="btn primary"
                        disabled={busy === `model-${p.id}` || !(draft.model || p.model)}
                        onClick={() => saveModel(p)}
                      >
                        {busy === `model-${p.id}` ? "Saving…" : "Use this model"}
                      </button>
                    )}
                    {p.via !== "env" && (
                      <button
                        type="button"
                        className="btn ghost"
                        disabled={busy === p.id}
                        onClick={() => disconnect(p.id, p.name)}
                      >
                        Disconnect
                      </button>
                    )}
                    {p.via === "env" && <span className="hint">Model is chosen per bot when using an env key.</span>}
                  </div>
                </>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
