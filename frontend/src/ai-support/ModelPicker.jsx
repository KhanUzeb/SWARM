import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiJson } from "../lib.js";

function labelOf(model) {
  if (!model) return "";
  if (model.name && model.name !== model.id) return model.name;
  return model.id;
}

export default function ModelPicker({
  id,
  token,
  value,
  onChange,
  providerId,
  apiKey,
  placeholder = "Search models",
  disabled = false,
  autoSelectFirst = false,
  onModelsLoaded,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [live, setLive] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [models, setModels] = useState([]);
  const [active, setActive] = useState(0);
  const rootRef = useRef(null);
  const inputRef = useRef(null);
  const fetchGen = useRef(0);
  const autoSelected = useRef(false);

  const applyModels = useCallback((list, meta = {}) => {
    setModels(list);
    setLive(!!meta.live);
    setNote(meta.note || "");
    setError(meta.error || "");
    onModelsLoaded?.(list, meta);
    if (autoSelectFirst && !autoSelected.current && list.length && !value) {
      const preferred = meta.defaultModel
        || list.find(m => m.default)?.id
        || list[0]?.id;
      if (preferred) {
        autoSelected.current = true;
        onChange?.(preferred);
      }
    }
  }, [autoSelectFirst, onChange, onModelsLoaded, value]);

  const load = useCallback(async () => {
    if (!token) return;
    const gen = ++fetchGen.current;
    setLoading(true);
    setError("");
    try {
      if (providerId) {
        const key = (apiKey || "").trim();
        if (key.length >= 8) {
          const res = await api(`/api/ai-support/providers/${providerId}/models`, {
            token,
            method: "POST",
            body: { api_key: key },
          });
          const data = res.ok ? await res.json() : null;
          if (gen !== fetchGen.current) return;
          applyModels(data?.models || [], {
            live: !!data?.live,
            note: data?.note,
            error: data?.error,
            defaultModel: data?.default_model,
          });
          setLoading(false);
          return;
        }
        const res = await apiJson(`/api/v2/providers/${providerId}/models`, { token, cacheTtl: 0 });
        if (gen !== fetchGen.current) return;
        const data = res.ok ? res.data : null;
        applyModels(data?.models || [], {
          live: !!data?.live,
          note: data?.note,
          error: data?.error,
          defaultModel: data?.default_model,
        });
      } else {
        const res = await apiJson("/api/v2/models/connected", { token, cacheTtl: 0 });
        if (gen !== fetchGen.current) return;
        const data = res.ok ? res.data : null;
        applyModels(data?.models || [], {
          live: !!data?.live,
          note: data?.note,
          defaultModel: data?.default_model,
        });
      }
    } catch {
      if (gen !== fetchGen.current) return;
      applyModels([], { live: false, note: "Couldn't load models." });
    }
    setLoading(false);
  }, [token, providerId, apiKey, applyModels]);

  useEffect(() => {
    autoSelected.current = false;
    load();
  }, [load]);

  useEffect(() => {
    if (!apiKey || apiKey.trim().length < 8) return undefined;
    const t = setTimeout(load, 350);
    return () => clearTimeout(t);
  }, [load, apiKey]);

  useEffect(() => {
    if (!open) return undefined;
    function onDoc(ev) {
      if (!rootRef.current?.contains(ev.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = models.slice();
    if (value && !list.some((m) => m.id === value)) {
      list.unshift({ id: value, name: value, custom: true });
    }
    if (!q) return list;
    return list.filter((m) => {
      const hay = `${m.id} ${m.name || ""} ${m.provider_name || m.provider_id || ""}`.toLowerCase();
      return hay.includes(q);
    });
  }, [models, query, value]);

  useEffect(() => { setActive(0); }, [query, open, filtered.length]);

  function pick(modelId) {
    onChange?.(modelId);
    setQuery("");
    setOpen(false);
  }

  function onKey(ev) {
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (ev.key === "Enter") {
      ev.preventDefault();
      if (filtered[active]) pick(filtered[active].id);
      else if (query.trim()) pick(query.trim());
    } else if (ev.key === "Escape") {
      setOpen(false);
    }
  }

  const current = models.find((m) => m.id === value);
  const badge = loading ? "Loading…" : live ? "Live API" : models.length ? "Catalog" : "No models";

  return (
    <div className="model-picker" ref={rootRef}>
      <button
        type="button"
        id={id}
        className="model-picker-toggle"
        disabled={disabled || loading}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
          setTimeout(() => inputRef.current?.focus(), 0);
        }}
      >
        <span className="model-picker-value">
          {current ? labelOf(current) : (value || placeholder)}
        </span>
        <span className={`model-live${live ? " on" : ""}`}>{badge}</span>
        <span className="model-picker-chevron" aria-hidden="true">⌄</span>
      </button>
      {open && (
        <div className="model-picker-pop">
          <input
            ref={inputRef}
            className="model-picker-search"
            value={query}
            placeholder={placeholder}
            onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
            onKeyDown={onKey}
            autoComplete="off"
            spellCheck={false}
          />
          <ul className="model-picker-list" role="listbox" aria-label="Models">
            {loading && !filtered.length && <li className="empty-state">Fetching models from API…</li>}
            {!loading && !filtered.length && (
              <li className="empty-state">{note || "No models match."}</li>
            )}
            {filtered.slice(0, 80).map((m, i) => (
              <li key={`${m.provider_id || ""}:${m.id}`}>
                <button
                  type="button"
                  role="option"
                  aria-selected={m.id === value}
                  className={`model-option${i === active ? " active" : ""}${m.id === value ? " selected" : ""}`}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => pick(m.id)}
                >
                  <span className="model-option-name">{labelOf(m)}</span>
                  <span className="model-option-id">
                    {m.provider_name ? `${m.provider_name} · ` : ""}{m.id}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="model-picker-foot">
            {error ? <span className="hint error">{error}</span> : <span className="hint">{note || (live ? "Fetched from provider API" : "Connect a key to load live models")}</span>}
            <button type="button" className="btn ghost" onClick={load} disabled={loading}>
              Refresh
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
