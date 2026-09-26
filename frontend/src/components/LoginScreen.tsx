import * as React from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Lamp } from "@/components/Flap";
import { Shield } from "lucide-react";

interface LoginScreenProps {
  onLogin: (handle: string, pass: string) => Promise<void> | void;
  onSetup: (handle: string, pass: string) => Promise<void> | void;
  status?: string;
  demoMode?: boolean;
}

export function LoginScreen({ onLogin, onSetup, status, demoMode }: LoginScreenProps) {
  const [handle, setHandle] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [mode, setMode] = React.useState<"login" | "setup">("login");
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!handle.trim()) {
      setError("Enter the handle you registered with.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (mode === "login") {
        await onLogin(handle.trim(), password);
      } else {
        await onSetup(handle.trim(), password);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Sign-in failed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const ready = status === "connected";

  return (
    <div id="login">
      <div className="login-card">
        <div className="login-head">
          <span className="login-mark">Swarm</span>
          <h1>{mode === "login" ? "Sign in to the desk" : "Create the workspace"}</h1>
          <p>
            {mode === "login"
              ? "Your teammates and their audit trail are waiting on the other side of this."
              : "The first account you create administers the workspace."}
          </p>
        </div>

        <form onSubmit={submit} className="login-form">
          <div className="login-row">
            <label className="field-label" htmlFor="login-handle">
              Handle
            </label>
            <Input
              id="login-handle"
              value={handle}
              onChange={(e) => setHandle(e.target.value)}
              placeholder="alice"
              autoFocus
              autoComplete="username"
            />
          </div>

          <div className="login-row">
            <label className="field-label" htmlFor="login-pass">
              Password
              {mode === "setup" && <span style={{ color: "var(--lamp-hold)" }}> · required</span>}
            </label>
            <Input
              id="login-pass"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === "setup" ? "Choose a password" : "Your password"}
              autoComplete={mode === "setup" ? "new-password" : "current-password"}
            />
          </div>

          {error && (
            <div className="login-error" role="alert">
              {error}
            </div>
          )}

          <Button type="submit" variant="primary" loading={busy} style={{ height: 34, width: "100%" }}>
            {mode === "login" ? "Sign in" : "Create workspace"}
          </Button>
        </form>

        {/* Advanced detail stays behind progressive disclosure. */}
        <details className="panel-note">
          <summary style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 6 }}>
            <Shield size={12} />
            How credentials are stored
          </summary>
          <p style={{ marginTop: 6, lineHeight: 1.55 }}>
            PBKDF2-HMAC-SHA256, 100k rounds, per-user salt. Only the hash is kept — the raw
            password is never written to the database.
          </p>
        </details>

        <div className="login-foot" style={{ borderTop: "1px solid var(--rule)", paddingTop: 12 }}>
          <span>Self-hosted</span>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Lamp tone={ready ? "go" : "off"} />
            {ready ? "Relay ready" : "Relay not ready"}
          </span>
        </div>

        {demoMode && (
          <Button variant="secondary" onClick={() => onLogin("demo", "")} style={{ width: "100%" }}>
            Open the demo desk
          </Button>
        )}

        <p className="panel-note" style={{ textAlign: "center" }}>
          {mode === "login" ? (
            <>
              No workspace yet?{" "}
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setMode("setup");
                  setError("");
                }}
              >
                Create one
              </button>
            </>
          ) : (
            <>
              Already registered?{" "}
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setMode("login");
                  setError("");
                }}
              >
                Sign in
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
