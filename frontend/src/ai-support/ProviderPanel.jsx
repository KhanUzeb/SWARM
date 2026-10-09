import { useCallback, useEffect, useState } from "react";
import { api, apiJson, bustCache, CACHE_TTL } from "../lib.js";
import { draftAfterSave, isUsableKey } from "./modelConnection.js";
import ModelPicker from "./ModelPicker";

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
    // Defect 1: the connect gate is the one predicate the live preview uses, so
    // a key that previewed models is provably a key that can connect them.
    if (!isUsableKey(key)) {
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
    // A model is only claimed active once the save succeeds: a failed save
    // reverts the draft to whatever the provider is already using, so an
    // unsaved pick never masquerades as the active model (defect 3).
    const commit = (ok) => setDrafts((d) => ({
      ...d,
      [provider.id]: { ...draftFor(provider), ...draftAfterSave({ requested: model, saved: model, ok, serverModel: provider.model || "" }) },
    }));
    try {
      const res = await api(`/api/ai-support/connect/${provider.id}`, {
        token,
        method: "PATCH",
        body: { model },
      });
      if (res.ok) {
        flash?.(`Using ${model} on ${provider.name}`);
        commit(true);
        bustCache("/api/status", "/api/ai-support");
        await load();
        onStatusChange?.();
      } else {
        flash?.("Couldn't save that model", true);
        commit(false);
      }
    } catch {
      flash?.("Couldn't save that model", true);
      commit(false);
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
          if (p.id === "custom") return <CustomProviderCard key="custom" provider={p} token={token} draft={draftFor(p)} busy={busy} setBusy={setBusy} setDrafts={setDrafts} draftFor={draftFor} flash={flash} load={load} onStatusChange={onStatusChange} />;
          const draft = draftFor(p);
          return (
            <li key={p.id} className={`provider-card${p.connected ? " connected" : ""}`}>
              <div className="provider-head">
                <span className="provider-name">{p.name}</span>
                <span className="provider-meta">{p.kind || "OpenAI compatible"}</span>
                <span className="provider-meta">{(p.auth_methods || ["api_key"]).join(" · ")}</span>
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
                      // The model belongs to the key it was chosen under; a new
                      // key means a new list, so the stale pick is dropped.
                      [p.id]: { key: e.target.value, model: "" },
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
                      [p.id]: { key: draft.key, model },
                    }))}
                    placeholder="Search this provider's models"
                  />
                  <div className="provider-actions">
                    {p.auth_methods?.includes("oauth") && p.oauth_configured && <button type="button" className="btn btn-ghost" onClick={() => startOAuth(p)}>Connect with OAuth</button>}
                    <a className="btn btn-ghost" href={p.key_url} target="_blank" rel="noreferrer">Get key</a>
                    <button
                      type="button"
                      className="btn btn-primary"
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
                        className="btn btn-primary"
                        disabled={busy === `model-${p.id}` || !(draft.model || p.model)}
                        onClick={() => saveModel(p)}
                      >
                        {busy === `model-${p.id}` ? "Saving…" : "Use this model"}
                      </button>
                    )}
                    {p.via !== "env" && (
                      <button
                        type="button"
                        className="btn btn-ghost"
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
      <details className="provider-advanced">
        <summary>Advanced: self-hosted endpoints &amp; env fallbacks</summary>
        <p className="hint">
          Operators can point the Custom provider at Ollama, LM Studio, vLLM, or any
          OpenAI-compatible gateway with <code>SWARM_OPENAI_COMPAT_BASE_URL</code> and{" "}
          <code>SWARM_OPENAI_COMPAT_API_KEY</code>. UI-connected keys stay encrypted in
          SQLite and are never returned by the API.
        </p>
      </details>
    </div>
  );
}

function CustomProviderCard({ provider: p, token, draft, busy, setBusy, setDrafts, draftFor, flash, load, onStatusChange }) {
  async function connectCustom() {
    const key = (draft.key || "").trim();
    const model = (draft.model || "").trim();
    if (!model) {
      flash?.("Pick or type the model id your server serves", true);
      return;
    }
    if (key && !isUsableKey(key)) {
      flash?.("Key looks too short — clear it for keyless local servers", true);
      return;
    }
    setBusy(p.id);
    try {
      const res = await api(`/api/ai-support/connect/${p.id}`, {
        token,
        method: "POST",
        body: { api_key: key || "local-no-key", model },
      });
      if (res.ok) {
        flash?.(`Custom endpoint using ${model}`);
        setDrafts((d) => ({ ...d, [p.id]: { key: "", model: d[p.id]?.model } }));
        bustCache("/api/status", "/api/ai-support");
        await load();
        onStatusChange?.();
      } else flash?.("Couldn't connect the custom endpoint", true);
    } catch {
      flash?.("Couldn't connect the custom endpoint", true);
    }
    setBusy(null);
  }

  return (
    <li className={`provider-card${p.connected ? " connected" : ""}`}>
      <div className="provider-head">
        <span className="provider-name">{p.name}</span>
        <span className="provider-meta">Ollama · LM Studio · vLLM · any OpenAI-compatible server</span>
        <span className={`provider-badge${p.connected ? " on" : ""}`}>
          {p.connected ? (p.via === "env" ? "Env" : "Connected") : "Not connected"}
        </span>
      </div>
      <p className="provider-desc">
        Bring your own model server. Base URL comes from{" "}
        <code>SWARM_OPENAI_COMPAT_BASE_URL</code> (default{" "}
        <code>http://127.0.0.1:11434/v1</code>). Leave the key empty for keyless
        local servers.
      </p>
      {!p.connected ? (
        <>
          <label className="field-label" htmlFor="key-custom">API key (optional for local)</label>
          <input
            id="key-custom"
            type="password"
            autoComplete="off"
            placeholder="Optional — blank for local servers"
            value={draft.key}
            onChange={(e) => setDrafts((d) => ({ ...d, [p.id]: { key: e.target.value, model: "" } }))}
          />
          <label className="field-label" htmlFor="model-custom">Model</label>
          {/* Defect 2: the custom card wired no picker, so a keyed server's
              models were invisible. The picker lists live models once a key is
              present; without one it accepts an exact typed id (Enter). */}
          <ModelPicker
            id="model-custom"
            token={token}
            providerId="custom"
            apiKey={draft.key}
            value={draft.model || ""}
            onChange={(model) => setDrafts((d) => ({ ...d, [p.id]: { key: draft.key, model } }))}
            placeholder="Search models, or type an exact id"
          />
          <div className="provider-actions">
            <button type="button" className="btn btn-primary" disabled={busy === p.id} onClick={connectCustom}>
              {busy === p.id ? "Connecting…" : "Connect endpoint"}
            </button>
          </div>
        </>
      ) : (
        <div className="provider-actions">
          <span className="hint">Model: {p.model || draft.model || "custom"}</span>
          {p.via !== "env" && (
            <button type="button" className="btn btn-ghost" disabled={busy === p.id} onClick={async () => {
              setBusy(p.id);
              try {
                const res = await api(`/api/ai-support/connect/${p.id}`, { token, method: "DELETE", json: false });
                if (res.ok) { flash?.("Disconnected custom endpoint"); bustCache("/api/status", "/api/ai-support"); await load(); onStatusChange?.(); }
                else flash?.("Couldn't disconnect", true);
              } catch { flash?.("Couldn't disconnect", true); }
              setBusy(null);
            }}>Disconnect</button>
          )}
        </div>
      )}
    </li>
  );
}
