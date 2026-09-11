import { fmtBytes, fmtTime } from "../ui.jsx";

/** First-class artifact object (plan §20). */
export function ArtifactCard({ artifact, onPreview, onOpen, onShare }) {
  const name = artifact?.name || "artifact";
  const kind = (artifact?.mime_type || artifact?.type || "file").split("/").pop().toUpperCase();
  const size = artifact?.size ?? artifact?.metadata?.size;
  return (
    <article className="artifact-card" aria-label={`Artifact ${name}`}>
      <div className="artifact-head">
        <span className="artifact-kind">{kind}</span>
        <span className="artifact-name truncate">{name}</span>
      </div>
      <div className="artifact-meta muted">
        {artifact?.creating_agent && <span>By {artifact.creating_agent} · </span>}
        {artifact?.created_at && <span>{fmtTime(artifact.created_at)} · </span>}
        {size != null && <span>{fmtBytes(size)}</span>}
        {artifact?.download_url && <span> · v{artifact?.version || 1}</span>}
      </div>
      <div className="artifact-actions">
        {artifact?.download_url && <a className="btn btn-secondary btn-sm" href={artifact.download_url}>Download</a>}
        {onPreview && <button className="btn btn-ghost btn-sm" onClick={onPreview}>Preview</button>}
        {onOpen && <button className="btn btn-ghost btn-sm" onClick={onOpen}>Open</button>}
        {onShare && <button className="btn btn-ghost btn-sm" onClick={onShare}>Share</button>}
      </div>
    </article>
  );
}

export function SourceChip({ index, label, href }) {
  return (
    <a className="source-chip" href={href || "#"} onClick={(e) => { if (!href) e.preventDefault(); }} title={label}>
      [{index}] {label}
    </a>
  );
}
