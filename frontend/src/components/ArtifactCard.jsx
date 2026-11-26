import { useState } from "react";
import { fmtBytes, fmtTime } from "../ui.jsx";
import { api } from "../lib.js";
import { apiErrorText } from "../work/workUtils.js";

/** Only the backend's own artifact route is an acceptable download target. */
export function artifactDownloadPath(url) {
  return typeof url === "string" && /^\/api\/v2\/artifacts\/[A-Za-z0-9_-]+$/.test(url)
    ? url : "";
}

/** Say what the backend refused, not just its status code. */
export function downloadErrorMessage(status, body) {
  if (status === 401) return "Session expired — sign in again";
  const detail = apiErrorText(body?.detail, "");
  return detail || `Download failed (${status})`;
}

/** First-class artifact object (plan §20). */
export function ArtifactCard({ artifact, onPreview, onOpen, onShare, token }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const name = artifact?.name || "artifact";
  // A hostile artifact row must not turn the button into a request to an
  // unrelated endpoint, even same-origin.
  const downloadPath = artifactDownloadPath(artifact?.download_url);

  async function download() {
    if (!downloadPath || busy) return;
    setBusy(true);
    setError("");
    try {
      // A plain <a href> cannot carry the Bearer token the artifact route
      // requires, so it opened a 401 JSON body instead of the file.
      const res = await api(downloadPath, { token });
      if (!res.ok) {
        let body = null;
        try { body = await res.json(); } catch { /* non-JSON error body */ }
        throw new Error(downloadErrorMessage(res.status, body));
      }
      const url = URL.createObjectURL(await res.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e.message || "Could not download artifact");
    } finally {
      setBusy(false);
    }
  }

  const kind = (artifact?.mime_type || artifact?.type || "file").split("/").pop().toUpperCase();
  const size = artifact?.size ?? artifact?.metadata?.size;
  // Defect: the fact line was stitched together with middle-dot meta strings
  // and printed separators as their own spans, so a missing field left an
  // orphan "·" behind (AGENTS.md 8a).
  const facts = [
    artifact?.creating_agent ? `By ${artifact.creating_agent}` : "",
    artifact?.created_at ? fmtTime(artifact.created_at) : "",
    size != null ? fmtBytes(size) : "",
    // Defect: the version badge was gated on download_url, an unrelated
    // column, so a versioned artifact with no URL silently showed no version.
    artifact?.version ? `v${artifact.version}` : "",
  ].filter(Boolean);
  return (
    <article className="artifact-card" aria-label={`Artifact ${name}`}>
      <div className="artifact-head">
        <span className="artifact-kind">{kind}</span>
        <span className="artifact-name truncate">{name}</span>
      </div>
      {facts.length > 0 && <div className="artifact-meta muted">{facts.join(", ")}</div>}
      <div className="artifact-actions">
        {downloadPath && (
          <button type="button" className="btn btn-secondary btn-sm" onClick={download} disabled={busy} aria-busy={busy}>
            {busy ? "Downloading…" : "Download"}
          </button>
        )}
        {onPreview && <button className="btn btn-ghost btn-sm" onClick={onPreview}>Preview</button>}
        {onOpen && <button className="btn btn-ghost btn-sm" onClick={onOpen}>Open</button>}
        {onShare && <button className="btn btn-ghost btn-sm" onClick={onShare}>Share</button>}
      </div>
      {error && <p className="error-text small" role="alert">{error}</p>}
    </article>
  );
}

/**
 * Only web and mail links are clickable. A source href is written by a bot, so
 * anything else — `javascript:`, `data:`, a bare scheme — is rendered as inert
 * text rather than a link that runs on click.
 */
function safeHref(href) {
  const text = String(href || "").trim();
  return /^(https?:\/\/|mailto:)/i.test(text) ? text : null;
}

export function SourceChip({ index, label, href }) {
  const to = safeHref(href);
  const body = <>[{index}] {label}</>;
  if (!to) return <span className="source-chip" title={label}>{body}</span>;
  return (
    <a className="source-chip" href={to} target="_blank" rel="noopener noreferrer" title={label}>
      {body}
    </a>
  );
}