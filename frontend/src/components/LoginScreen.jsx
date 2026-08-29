import { useState } from "react";
import { Button, Input, Card } from "../ui.jsx";

export function LoginScreen({ onLogin, onSetup, status, demoMode }) {
  const [handle, setHandle] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState("login");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (!handle.trim()) { setError("Enter a handle"); return; }
    setBusy(true); setError("");
    try {
      if (mode === "login") await onLogin(handle.trim(), password);
      else await onSetup(handle.trim(), password);
    } catch (err) {
      setError(err.message || "Something went wrong");
    } finally { setBusy(false); }
  }

  return (
    <div id="login">
      <div className="login-bg-grid" aria-hidden />
      <div className="login-card">
        <div className="login-brand">
          <span className="login-logo">swarm</span>
          <span className="login-tagline">AI agents as teammates</span>
        </div>

        <h1 className="login-title">Sign in to your workspace</h1>
        <p className="login-sub">Humans and agents in the same channels. One history, one audit trail.</p>

        <form onSubmit={submit} className="login-form">
          <label className="text-label" htmlFor="login-handle">Handle</label>
          <Input
            id="login-handle"
            value={handle}
            onChange={e => setHandle(e.target.value)}
            placeholder="your-name"
            autoFocus
            autoComplete="username"
          />

          <label className="text-label" htmlFor="login-pass">Password {mode === "setup" && <span className="text-subtle">(new)</span>}</label>
          <Input
            id="login-pass"
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder={mode === "setup" ? "Create a password" : "Your password"}
            autoComplete={mode === "setup" ? "new-password" : "current-password"}
          />

          {error && <p className="login-error text-error text-sm">{error}</p>}

          <Button type="submit" variant="primary" size="lg" loading={busy} className="login-submit">
            {mode === "login" ? "Sign in" : "Create workspace"}
          </Button>
        </form>

        <div className="login-switch">
          {mode === "login" ? (
            <span>New here? <button className="link-btn" onClick={() => { setMode("setup"); setError(""); }}>Create a workspace</button></span>
          ) : (
            <span>Already registered? <button className="link-btn" onClick={() => { setMode("login"); setError(""); }}>Sign in instead</button></span>
          )}
        </div>

        {demoMode && (
          <button className="login-demo" onClick={() => onLogin("demo", "")}>
            <span className="login-demo-icon">✨</span> Enter demo mode
          </button>
        )}
      </div>

      <div className="login-foot">
        <span>Self-hosted · Open source</span>
        <span>Spec: SPEC.md · VISION.md</span>
      </div>
    </div>
  );
}
