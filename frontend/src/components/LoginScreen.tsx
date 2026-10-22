import * as React from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Lamp } from "@/components/Flap";
import { Shield } from "lucide-react";

type SetupAccess =
  | { kind: "bootstrap"; value: string }
  | { kind: "invite"; value: string }
  | { kind: "either"; value: string };

interface LoginScreenProps {
  onLogin: (handle: string, pass: string) => Promise<void> | void;
  onSetup: (handle: string, pass: string, access: SetupAccess) => Promise<void> | void;
  /** Whether the backend answered the public status probe. This screen never
   *  has a chat socket — the lamp used to read that and so sat on "Relay not
   *  ready" over a perfectly healthy server, for ever. */
  relayReady?: boolean;
  /** From GET /api/status: has this workspace been set up yet? */
  onboarded?: boolean | null;
  demoMode?: boolean;
}

export function LoginScreen({ onLogin, onSetup, relayReady, onboarded, demoMode }: LoginScreenProps) {
  const [handle, setHandle] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [accessCode, setAccessCode] = React.useState("");
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
        const kind = onboarded === false ? "bootstrap" : onboarded === true ? "invite" : "either";
        await onSetup(handle.trim(), password, { kind, value: accessCode.trim() });
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Sign-in failed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  // The demo desk was the one button here that could not report a failure: it
  // called onLogin straight from onClick, so a refused or offline server threw
  // into nothing and the screen sat there looking untouched.
  async function openDemo() {
    setBusy(true);
    setError("");
    try {
      await onLogin("demo", "");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "The demo desk could not be opened.");
    } finally {
      setBusy(false);
    }
  }

  // There is one endpoint behind both modes, so "Create the workspace" and "The
  // first account you create administers it" were only ever true for the very
  // first handle. Once the workspace exists a new handle joins as a member.
  const firstAccount = onboarded === false;

  return (
    <div id="login">
      <div className="login-card">
        <div className="login-head">
          <span className="login-mark">Swarm</span>
          <h1>{mode === "login" ? "Sign in to the desk" : firstAccount ? "Create the workspace" : "Add an account"}</h1>
          <p>
            {mode === "login"
              ? "Your teammates and their audit trail are waiting on the other side of this."
              : firstAccount
                ? "The first account you create administers the workspace."
                : "This workspace already has accounts. A new handle joins as a member."}
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
              {/* The backend only asks for a minimum length, and only on the
                  first account — "required" was true for neither of the two
                  paths it was shown on. */}
              {mode === "setup" && firstAccount && <span className="reg reg-hold"> optional; at least 8 characters if set</span>}
            </label>
            <Input
              id="login-pass"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === "setup" ? "Choose a password" : "Your password"}
              minLength={mode === "setup" && firstAccount ? 8 : undefined}
              autoComplete={mode === "setup" ? "new-password" : "current-password"}
            />
          </div>

          {mode === "setup" && (
            <details className="panel-note">
              <summary style={{ cursor: "pointer" }}>
                Remote setup or account invite
              </summary>
              <p style={{ marginTop: 6, lineHeight: 1.55 }}>
                Use the setup token for the first administrator or the invite code for another account.
              </p>
              <div className="login-row">
                <label className="field-label" htmlFor="login-access-code">
                  {onboarded === false ? "Setup token" : onboarded === true ? "Invite code" : "Setup token or invite code"}
                </label>
                <Input
                  id="login-access-code"
                  type="password"
                  value={accessCode}
                  onChange={(e) => setAccessCode(e.target.value)}
                  autoComplete="off"
                />
              </div>
            </details>
          )}

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
            <Lamp tone={relayReady ? "go" : "off"} />
            {relayReady ? "Relay ready" : "Relay not ready"}
          </span>
        </div>

        {demoMode && (
          <Button variant="secondary" onClick={openDemo} disabled={busy} loading={busy} style={{ width: "100%" }}>
            Open the demo desk
          </Button>
        )}

        <p className="panel-note" style={{ textAlign: "center" }}>
          {mode === "login" ? (
            <>
              No handle yet?{" "}
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setMode("setup");
                  setError("");
                }}
              >
                {firstAccount ? "Create the workspace" : "Add an account"}
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
                  setAccessCode("");
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
