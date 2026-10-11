import { fmtBytes, fmtTime } from "../ui.jsx";

/** First-class artifact object (plan §20). */
export function ArtifactCard({ artifact, onPreview, onOpen, onShare }) {
  const name = artifact?.name || "artifact";
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
        {artifact?.download_url && <a className="btn btn-secondary btn-sm" href={artifact.download_url} rel="noreferrer">Download</a>}
        {onPreview && <button className="btn btn-ghost btn-sm" onClick={onPreview}>Preview</button>}
        {onOpen && <button className="btn btn-ghost btn-sm" onClick={onOpen}>Open</button>}
        {onShare && <button className="btn btn-ghost btn-sm" onClick={onShare}>Share</button>}
      </div>
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