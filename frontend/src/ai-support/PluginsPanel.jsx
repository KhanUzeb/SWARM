import { useCallback, useEffect, useState } from "react";
import { api, apiJson } from "../lib.js";

export default function PluginsPanel({ token, flash, meRole }) {
  const [plugins, setPlugins] = useState([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiJson("/api/tools", { token });
      if (res.ok) setPlugins(res.data?.plugins || []);
    } catch {
      setPlugins([]);
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  async function reloadPlugins() {
    if (meRole !== "admin") {
      flash?.("Only admins can reload plugins", true);
      return;
    }
    setBusy(true);
    try {
      const res = await api("/api/plugins/reload", { token, method: "POST", body: {} });
      if (res.ok) {
        flash?.("Plugins reloaded");
        await load();
      } else flash?.("Couldn't reload plugins", true);
    } catch {
      flash?.("Couldn't reload plugins", true);
    }
    setBusy(false);
  }

  return (
    <div className="panel-body plugins-panel">
      <p className="panel-note">
        Drop manifests in <code>plugins/</code> on the host. Each plugin can register tools and hooks for every bot.
      </p>
      <div className="panel-section-head">
        <span className="mem-title">Loaded plugins ({plugins.length})</span>
        {meRole === "admin" && (
          <button type="button" className="btn ghost btn-sm" disabled={busy} onClick={reloadPlugins}>
            {busy ? "Reloading…" : "Reload plugins"}
          </button>
        )}
      </div>
      {!plugins.length && <p className="empty-state">No plugins loaded yet.</p>}
      <ul className="plugin-grid">
        {plugins.map(p => (
          <li key={p.id} className="plugin-card">
            <div className="plugin-card-head">
              <span className="plugin-name">{p.name}</span>
              <span className="plugin-version">v{p.version || "0"}</span>
            </div>
            <p className="plugin-desc">{p.description || "No description"}</p>
            {p.tools?.length > 0 && (
              <div className="plugin-tools">
                {p.tools.map(t => <span key={t} className="tool-tag plugin">{t}</span>)}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
