import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiJson, fmtBytes } from "../lib.js";

function prettyPath(path, home) {
  const raw = String(path || "");
  const prefix = String(home || "");
  if (prefix && raw.toLowerCase().startsWith(prefix.toLowerCase())) {
    return `~${raw.slice(prefix.length).replaceAll("\\", "/") || ""}`;
  }
  return raw;
}

export default function SystemPanel({ token, flash, onRootChange }) {
  const [listing, setListing] = useState(null);
  const [path, setPath] = useState("");
  const [address, setAddress] = useState("");
  const [preview, setPreview] = useState("");
  const [previewName, setPreviewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const reqSeq = useRef(0);
  const flashRef = useRef(flash);
  flashRef.current = flash;

  const load = useCallback(async (nextPath = "") => {
    if (!token) return;
    const seq = ++reqSeq.current;
    setBusy(true);
    try {
      const res = await apiJson(
        `/api/computer/system?path=${encodeURIComponent(nextPath)}`,
        { token },
      );
      if (seq !== reqSeq.current) return;
      const data = res.ok ? res.data : null;
      setListing(data);
      setAddress(data?.absolute || data?.root || "");
      if (!res.ok) flashRef.current?.("Couldn't list this machine", true);
    } catch {
      if (seq !== reqSeq.current) return;
      setListing(null);
      flashRef.current?.("Couldn't list this machine", true);
    }
    if (seq === reqSeq.current) setBusy(false);
  }, [token]);

  useEffect(() => { load(path); }, [load, path]);

  async function bindRoot(nextRoot) {
    if (!nextRoot) return;
    const seq = ++reqSeq.current;
    setBusy(true);
    try {
      const res = await api("/api/computer/system/root", {
        token,
        method: "POST",
        body: { path: nextRoot },
      });
      const data = res.ok ? await res.json() : null;
      if (seq !== reqSeq.current) return;
      if (!res.ok || !data) {
        flashRef.current?.("Couldn't open that folder", true);
        setBusy(false);
        return;
      }
      setListing(data);
      setAddress(data?.absolute || data?.root || "");
      setPath("");
      setPreview("");
      setPreviewName("");
      setQuery("");
      onRootChange?.();
      flashRef.current?.(`Now working in ${data.root}`);
    } catch {
      if (seq !== reqSeq.current) return;
      flashRef.current?.("Couldn't open that folder", true);
    }
    if (seq === reqSeq.current) setBusy(false);
  }

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

  function goAddress(ev) {
    ev?.preventDefault();
    const next = address.trim();
    if (!next) return;
    const root = listing?.root || "";
    const abs = listing?.absolute || "";
    if (root && next.replaceAll("\\", "/").toLowerCase() === root.replaceAll("\\", "/").toLowerCase()) {
      setPath("");
      return;
    }
    if (abs && next.replaceAll("\\", "/").toLowerCase() === abs.replaceAll("\\", "/").toLowerCase()) {
      return;
    }
    bindRoot(next);
  }

  const enabled = listing?.enabled !== false;
  const entries = (listing?.entries || []).filter((entry) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return String(entry.name || "").toLowerCase().includes(q);
  });
  const crumbs = listing?.crumbs || [];
  const places = listing?.places || [];

  return (
    <div className="panel-body system-panel">
      <p className="panel-note">
        This machine — not just the project. Jump to Home or Desktop, type any folder,
        or go up a level. Bots follow this folder with <code>system_run</code>,
        <code> system_read</code>, and <code>system_write</code>.
      </p>
      <div className="place-chips">
        <span className={`place-chip${enabled ? " on" : ""}`}>
          {enabled ? "On" : "Off"}
        </span>
        {places.map((place) => (
          <button
            key={place.id}
            type="button"
            className={`place-chip btn-chip${listing?.root === place.path ? " current" : ""}`}
            title={place.path}
            onClick={() => bindRoot(place.path)}
          >
            {place.label}
          </button>
        ))}
      </div>
      {!enabled && (
        <p className="panel-note">{listing?.note || "System tools are disabled."}</p>
      )}
      {enabled && (
        <>
          <form className="sys-address" onSubmit={goAddress}>
            <label className="sr-only" htmlFor="sys-path">Folder path</label>
            <input
              id="sys-path"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              placeholder="C:\\Users\\… or ~/Documents"
              spellCheck={false}
              autoComplete="off"
            />
            <button type="submit" className="btn primary" disabled={busy}>Go</button>
            {listing?.can_go_up && listing?.parent_abs && !listing?.path && (
              <button
                type="button"
                className="btn"
                onClick={() => bindRoot(listing.parent_abs)}
                title={listing.parent_abs}
              >
                Up
              </button>
            )}
          </form>
          <div className="sys-toolbar">
            <nav className="path-crumb" aria-label="System path">
              {crumbs.map((c, i, all) => (
                <span key={c.absolute || c.path || "."}>
                  {i > 0 && <span className="crumb-sep">/</span>}
                  <button
                    type="button"
                    className={`crumb${i === all.length - 1 ? " current" : ""}`}
                    onClick={() => {
                      setPreview("");
                      setPath(c.path);
                    }}
                    title={c.absolute}
                  >
                    {c.label}
                  </button>
                </span>
              ))}
            </nav>
            <input
              className="sys-filter"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter this folder"
              autoComplete="off"
            />
          </div>
          {Boolean(listing?.path) && (
            <button type="button" className="file-row" onClick={() => { setPreview(""); setPath(listing.parent || ""); }}>
              <span className="file-kind">↑</span>
              <span className="file-name">..</span>
              <span className="file-size">parent</span>
            </button>
          )}
          {!listing?.path && listing?.can_go_up && listing?.parent_abs && (
            <button
              type="button"
              className="file-row"
              onClick={() => bindRoot(listing.parent_abs)}
            >
              <span className="file-kind">↑</span>
              <span className="file-name">..</span>
              <span className="file-size">{prettyPath(listing.parent_abs, listing?.home)}</span>
            </button>
          )}
          <ul className="file-list">
            {busy && !entries.length && <li className="empty-state">Listing…</li>}
            {!busy && !entries.length && !listing?.error && (
              <li className="empty-state">
                {query ? "Nothing matches that filter." : "Empty folder."}
              </li>
            )}
            {listing?.error && <li className="empty-state">{listing.error}</li>}
            {entries.map((entry) => (
              <li key={`${entry.kind}:${entry.path}:${entry.name}`}>
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
                  <span className={`file-kind ${entry.kind}`}>{entry.kind === "dir" ? "dir" : "file"}</span>
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
