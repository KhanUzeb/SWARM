import { useCallback, useEffect, useState } from "react";
import { api, apiJson, bustCache } from "../lib.js";

export default function AppsPanel({ token, flash }) {
  const [status, setStatus] = useState(null);
  const [key, setKey] = useState("");
  const [toolkit, setToolkit] = useState("");
  const [toolkits, setToolkits] = useState("");
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiJson("/api/composio/status", { token });
      setStatus(res.ok ? res.data : null);
    } catch {
      setStatus(null);
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  async function connect() {
    const apiKey = key.trim();
    if (apiKey.length < 8) {
      flash?.("Enter a Composio API key (8+ chars)", true);
      return;
    }
    setBusy("connect");
    try {
      const res = await api("/api/composio/connect", {
        token,
        method: "POST",
        body: { api_key: apiKey },
      });
      if (res.ok) {
        flash?.("Composio connected for this workspace");
        setKey("");
        bustCache("/api/status", "/api/composio");
        await load();
      } else flash?.("Couldn't connect Composio", true);
    } catch {
      flash?.("Couldn't connect Composio", true);
    }
    setBusy(null);
  }

  async function disconnect() {
    setBusy("disconnect");
    try {
      const res = await api("/api/composio/connect", { token, method: "DELETE", json: false });
      if (res.ok) {
        flash?.("Composio key removed");
        bustCache("/api/status", "/api/composio");
        await load();
      } else flash?.("Couldn't disconnect Composio", true);
    } catch {
      flash?.("Couldn't disconnect Composio", true);
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

  return (
    <div className="panel-body">
      <p className="panel-note">
        Composio plugin — one API key for the whole workspace. Every Bot can then
        use Gmail, Slack, GitHub, Notion, and 1000+ other apps via
        <code> plugin:composio:*</code> tools. Sends still need
        <code> request_approval</code>.
      </p>
      <ul className="panel-list">
        <li>{status?.connected ? `Connected (${status.key_hint || "key set"})` : "Not connected"}</li>
        <li>Workspace user: {status?.user_id || "swarm-workspace"}</li>
        <li>Client: {status?.sdk ? "official SDK" : "REST v3.1"}</li>
      </ul>
      {!status?.connected ? (
        <form className="mini-form" onSubmit={(ev) => { ev.preventDefault(); connect(); }}>
          <input
            type="password"
            autoComplete="off"
            placeholder="COMPOSIO_API_KEY"
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
          <div className="modal-actions">
            <a className="btn ghost" href="https://platform.composio.dev" target="_blank" rel="noreferrer">Get key</a>
            <button type="submit" className="btn primary" disabled={busy === "connect"}>
              {busy === "connect" ? "Connecting…" : "Connect workspace"}
            </button>
          </div>
        </form>
      ) : (
        <button type="button" className="btn ghost" disabled={busy === "disconnect"} onClick={disconnect}>
          Disconnect stored key
        </button>
      )}
      <div className="mem-title">App toolkits</div>
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
          <button type="button" className="btn" disabled={busy === "toolkit"} onClick={connectToolkit}>
            {busy === "toolkit" ? "Starting…" : "Connect this app"}
          </button>
        </div>
      </form>
      {toolkits ? <pre id="file-preview">{toolkits}</pre> : null}
    </div>
  );
}
