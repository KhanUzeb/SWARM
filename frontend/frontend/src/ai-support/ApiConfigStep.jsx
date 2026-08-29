import { useEffect, useState } from "react";
import { DEFAULT_MODEL, api, apiJson, CACHE_TTL } from "../lib.js";
import ModelPicker from "./ModelPicker.jsx";

/**
 * Onboarding step 1 — connect Groq (recommended) or OpenRouter.
 * Keys are sent once to the server and never stored in localStorage.
 */
export default function ApiConfigStep({ token, onContinue, onSkip, flash }) {
  const [providers, setProviders] = useState([]);
  const [groqKey, setGroqKey] = useState("");
  const [orKey, setOrKey] = useState("");
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [demo, setDemo] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      const pub = await apiJson("/api/status", { cacheTtl: CACHE_TTL.status });
      setDemo(!!pub.data?.demo);
      if (!token) return;
      const res = await apiJson("/api/ai-support/providers", { token, cacheTtl: CACHE_TTL.catalog });
      setProviders(res.ok ? res.data : []);
    })();
  }, [token]);

  const groqConnected = providers.some((p) => p.id === "groq" && p.connected);
  const orConnected = providers.some((p) => p.id === "openrouter" && p.connected);
  const ready = demo || groqConnected || orConnected;

  async function saveAndContinue(ev) {
    ev?.preventDefault();
    setBusy(true);
    try {
      if (groqKey.trim().length >= 8) {
        const res = await api("/api/ai-support/connect/groq", {
          token,
          method: "POST",
          body: { api_key: groqKey.trim(), model },
        });
        if (!res.ok) {
          flash?.("Couldn't save Groq key", true);
          setBusy(false);
          return;
        }
        setGroqKey("");
      }
      if (orKey.trim().length >= 8) {
        const res = await api("/api/ai-support/connect/openrouter", {
          token,
          method: "POST",
          body: { api_key: orKey.trim(), model },
        });
        if (!res.ok) {
          flash?.("Couldn't save OpenRouter key", true);
          setBusy(false);
          return;
        }
        setOrKey("");
      }
      onContinue?.();
    } catch {
      flash?.("Couldn't save API config", true);
    }
    setBusy(false);
  }

  return (
    <form onSubmit={saveAndContinue}>
      <p className="kicker">Step 1 of 3</p>
      <h1>Connect your AI</h1>
      <p>
        Paste an API key — it is encrypted and stored on the server only, never in the browser cache.
        {demo && " Demo mode is on, so you can skip this."}
      </p>

      <label htmlFor="onboard-groq">Groq API key <span className="optional">(recommended)</span></label>
      <input
        id="onboard-groq"
        type="password"
        autoComplete="off"
        placeholder={groqConnected ? "Already connected — paste to replace" : "gsk_…"}
        value={groqKey}
        onChange={(e) => setGroqKey(e.target.value)}
      />
      <a className="hint linkish" href="https://console.groq.com/keys" target="_blank" rel="noreferrer">Get a Groq key</a>

      <label htmlFor="onboard-model">Default model</label>
      <ModelPicker
        id="onboard-model"
        token={token}
        providerId={groqKey.trim().length >= 8 || groqConnected ? "groq" : (orKey.trim().length >= 8 || orConnected ? "openrouter" : undefined)}
        apiKey={groqKey.trim().length >= 8 ? groqKey : (orKey.trim().length >= 8 ? orKey : undefined)}
        value={model}
        onChange={setModel}
        placeholder={DEFAULT_MODEL}
      />

      <label htmlFor="onboard-or">OpenRouter key <span className="optional">(optional fallback)</span></label>
      <input
        id="onboard-or"
        type="password"
        autoComplete="off"
        placeholder={orConnected ? "Already connected" : "sk-or-…"}
        value={orKey}
        onChange={(e) => setOrKey(e.target.value)}
      />

      <div className="modal-actions onboarding-actions">
        <button type="button" className="btn ghost" onClick={onSkip}>Skip for now</button>
        <button type="submit" className="btn primary" disabled={busy || (!ready && groqKey.trim().length < 8 && orKey.trim().length < 8)}>
          {busy ? "Saving…" : ready && !groqKey && !orKey ? "Continue" : "Save & continue"}
        </button>
      </div>
    </form>
  );
}
