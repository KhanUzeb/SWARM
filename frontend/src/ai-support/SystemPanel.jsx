import { useCallback, useEffect, useState } from "react";
import { apiJson, fmtBytes } from "../lib.js";

function crumbs(path) {
  const parts = String(path || "").split("/").filter(Boolean);
  const items = [{ label: ".", path: "" }];
  let acc = "";
  for (const part of parts) {
    acc = acc ? `${acc}/${part}` : part;
    items.push({ label: part, path: acc });
  }
  return items;
}

export default function SystemPanel({ token, flash }) {
  const [listing, setListing] = useState(null);
  const [path, setPath] = useState("");
  const [preview, setPreview] = useState("");
  const [previewName, setPreviewName] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (nextPath = "") => {
    if (!token) return;
    setBusy(true);
    try {
      const res = await apiJson(
        `/api/computer/system?path=${encodeURIComponent(nextPath)}`,
        { token },
      );
      setListing(res.ok ? res.data : null);
      if (!res.ok) flash?.("Couldn't list this machine", true);
    } catch {
      setListing(null);
      flash?.("Couldn't list this machine", true);
    }
    setBusy(false);
  }, [token, flash]);

  useEffect(() => { load(path); }, [load, path]);

  async function openFile(filePath, name) {
    try {
      const res = await apiJson(
        `/api/computer/system/file?path=${encodeURIComponent(filePath)}`,
        { token },
      );
      if (!res.ok) throw new Error("file");
      setPreview(res.data.content);
      setPreviewName(name || filePath);
    } catch {
      flash?.("Couldn't open that file", true);
    }
  }

  const enabled = listing?.enabled !== false;
  const entries = listing?.entries || [];

  return (
    <div className="panel-body">
      <p className="panel-note">
        This machine, bound to <code>{listing?.root || "SWARM_SYSTEM_ROOT"}</code>.
        Ask a Bot to <code>system_run</code>, <code>system_read</code>, or{" "}
        <code>system_write</code>. The Sandbox tab is the isolated temp workspace.
        Set <code>SWARM_SYSTEM=0</code> to disable.
      </p>
      <div className="place-chips">
        <span className={`place-chip${enabled ? " on" : ""}`}>
          {enabled ? "On" : "Off"}
        </span>
        <span className="place-chip path" title={listing?.root || ""}>
          {listing?.root || "…"}
        </span>
      </div>
      {!enabled && (
        <p className="panel-note">{listing?.note || "System tools are disabled."}</p>
      )}
      {enabled && (
        <>
          <nav className="path-crumb" aria-label="System path">
            {crumbs(listing?.path || path).map((c, i, all) => (
              <span key={c.path || "."}>
                {i > 0 && <span className="crumb-sep">/</span>}
                <button
                  type="button"
                  className={`crumb${i === all.length - 1 ? " current" : ""}`}
                  onClick={() => { setPreview(""); setPath(c.path); }}
                >
                  {c.label}
                </button>
              </span>
            ))}
          </nav>
          {Boolean(listing?.path) && (
            <button type="button" className="file-row" onClick={() => { setPreview(""); setPath(listing.parent || ""); }}>
              <span className="file-kind">↑</span>
              <span className="file-name">..</span>
              <span className="file-size">parent</span>
            </button>
          )}
          <ul className="file-list">
            {busy && !entries.length && <li className="empty-state">Listing…</li>}
            {!busy && !entries.length && !listing?.error && (
              <li className="empty-state">Empty folder. Ask a Bot to write a file here.</li>
            )}
            {listing?.error && <li className="empty-state">{listing.error}</li>}
            {entries.map((entry) => (
              <li key={entry.path}>
                <button
                  type="button"
                  className="file-row"
                  onClick={() => {
                    if (entry.kind === "dir") {
                      setPreview("");
                      setPath(entry.path);
                    } else {
                      openFile(entry.path, entry.name);
                    }
                  }}
                >
                  <span className="file-kind">{entry.kind === "dir" ? "dir" : "file"}</span>
                  <span className="file-name">{entry.name}{entry.kind === "dir" ? "/" : ""}</span>
                  <span className="file-size">{entry.kind === "dir" ? "" : fmtBytes(entry.size)}</span>
                </button>
              </li>
            ))}
          </ul>
          {previewName && (
            <div className="preview-head">
              <strong>{previewName}</strong>
              <button type="button" className="btn ghost" onClick={() => { setPreview(""); setPreviewName(""); }}>
                Close
              </button>
            </div>
          )}
          {preview && <pre id="file-preview">{preview}</pre>}
        </>
      )}
    </div>
  );
}
