import * as React from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sparkles, Shield, ArrowRight } from "lucide-react";

interface LoginScreenProps {
  onLogin: (handle: string, pass: string) => Promise<void> | void;
  onSetup: (handle: string, pass: string) => Promise<void> | void;
  status?: string;
  demoMode?: boolean;
}

export function LoginScreen({
  onLogin,
  onSetup,
  status,
  demoMode,
}: LoginScreenProps) {
  const [handle, setHandle] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [mode, setMode] = React.useState<"login" | "setup">("login");
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!handle.trim()) {
      setError("Please enter a handle");
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
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("Something went wrong");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      id="login"
      className="min-h-screen w-full flex flex-col items-center justify-center p-4 bg-zinc-950 text-zinc-100 relative overflow-hidden select-none"
    >
      {/* Background Subtle Gradient Glow */}
      <div className="absolute top-1/4 -translate-y-1/2 left-1/2 -translate-x-1/2 w-[600px] h-[350px] bg-violet-600/10 blur-[140px] pointer-events-none rounded-full" />

      {/* Hero Header */}
      <div className="flex flex-col items-center text-center space-y-2 mb-8 z-10">
        <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-gradient-to-br from-violet-600 to-indigo-700 text-white font-bold text-xl shadow-lg shadow-violet-950/50 mb-1">
          S
        </div>
        <h1 className="text-xl font-bold tracking-tight text-zinc-100">
          swarm
        </h1>
        <p className="text-xs text-zinc-400 max-w-xs leading-relaxed">
          AI teammates in your workspace with shared memory and visible audit trail.
        </p>
      </div>

      {/* Login Card */}
      <div className="w-full max-w-sm rounded-2xl border border-zinc-800 bg-zinc-900/80 backdrop-blur-xl p-6 sm:p-7 shadow-2xl z-10 space-y-5">
        <div className="space-y-1">
          <h2 className="text-sm font-semibold text-zinc-100">
            {mode === "login" ? "Sign in to workspace" : "Create your workspace"}
          </h2>
          <p className="text-xs text-zinc-400">
            {mode === "login"
              ? "Enter your handle and password to continue."
              : "Set up the first administrator handle and password."}
          </p>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <label
              htmlFor="login-handle"
              className="text-xs font-medium text-zinc-300"
            >
              Handle
            </label>
            <Input
              id="login-handle"
              value={handle}
              onChange={(e) => setHandle(e.target.value)}
              placeholder="e.g. alice"
              autoFocus
              autoComplete="username"
              className="h-9 bg-zinc-950/70 border-zinc-800 focus-visible:border-violet-500"
            />
          </div>

          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label
                htmlFor="login-pass"
                className="text-xs font-medium text-zinc-300"
              >
                Password
              </label>
              {mode === "setup" && (
                <span className="text-[10px] text-violet-400">required</span>
              )}
            </div>
            <Input
              id="login-pass"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={
                mode === "setup" ? "Create a password" : "Your password"
              }
              autoComplete={
                mode === "setup" ? "new-password" : "current-password"
              }
              className="h-9 bg-zinc-950/70 border-zinc-800 focus-visible:border-violet-500"
            />
          </div>

          {error && (
            <div className="p-2.5 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs">
              {error}
            </div>
          )}

          <Button
            type="submit"
            variant="primary"
            size="lg"
            loading={busy}
            className="w-full h-9 rounded-xl font-medium shadow-md shadow-violet-950/40 text-xs"
          >
            {mode === "login" ? "Sign in" : "Create workspace"}
          </Button>
        </form>

        {/* Security details disclosure */}
        <details className="text-[11px] text-zinc-500 cursor-pointer pt-1">
          <summary className="hover:text-zinc-400 transition-colors inline-flex items-center gap-1">
            <Shield className="w-3 h-3 text-zinc-600" />
            <span>How credentials are encrypted</span>
          </summary>
          <p className="mt-1.5 pl-4 text-zinc-400 leading-relaxed text-[11px]">
            PBKDF2-HMAC-SHA256 with 100k rounds and per-user salt. Raw credentials are never stored in plain text.
          </p>
        </details>

        {/* Switch mode */}
        <div className="pt-2 border-t border-zinc-800/80 text-center text-xs text-zinc-400">
          {mode === "login" ? (
            <span>
              New workspace?{" "}
              <button
                type="button"
                className="text-violet-400 hover:text-violet-300 hover:underline font-medium"
                onClick={() => {
                  setMode("setup");
                  setError("");
                }}
              >
                Create a workspace
              </button>
            </span>
          ) : (
            <span>
              Already registered?{" "}
              <button
                type="button"
                className="text-violet-400 hover:text-violet-300 hover:underline font-medium"
                onClick={() => {
                  setMode("login");
                  setError("");
                }}
              >
                Sign in
              </button>
            </span>
          )}
        </div>

        {/* Demo Mode Button */}
        {demoMode && (
          <button
            type="button"
            onClick={() => onLogin("demo", "")}
            className="w-full flex items-center justify-center gap-2 p-2 rounded-xl border border-zinc-800 bg-zinc-950/40 hover:bg-zinc-850 hover:text-zinc-100 text-xs text-zinc-400 transition-colors"
          >
            <Sparkles className="w-3.5 h-3.5 text-violet-400" />
            <span>Launch demo mode</span>
          </button>
        )}
      </div>

      {/* Footer Status */}
      <div className="mt-6 text-[11px] text-zinc-600 z-10 flex items-center gap-2">
        <span
          className={`w-1.5 h-1.5 rounded-full ${
            status === "connected" ? "bg-emerald-500" : "bg-zinc-600"
          }`}
        />
        <span>Self-hosted · {status === "connected" ? "Relay ready" : "Connecting…"}</span>
      </div>
    </div>
  );
}
