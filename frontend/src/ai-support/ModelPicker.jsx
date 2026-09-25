import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiJson } from "../lib.js";

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
  const [hasLoaded, setHasLoaded] = useState(false);
  const rootRef = useRef(null);
  const inputRef = useRef(null);
  const fetchGen = useRef(0);
  const autoSelected = useRef(false);
  const loadedSpec = useRef("");
  const prevProvider = useRef(Symbol("unset"));
  const spec = `${providerId || ""} ${(apiKey || "").trim()}`;

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

  // One fetch path for all three sources — models load lazily (first open
  // or Refresh), never on mount or while typing a key, so opening the
  // provider panel doesn't fan out live API calls per card per keystroke.
  const load = useCallback(async () => {
    if (!token || disabled) return;
    const gen = ++fetchGen.current;
    setLoading(true);
    setError("");
    try {
      const key = (apiKey || "").trim();
      const path = providerId
        ? `/api/v2/providers/${providerId}/models`
        : "/api/v2/models/connected";
      const res = providerId && key.length >= 8
        ? await apiJson(`/api/ai-support/providers/${providerId}/models`, {
            token, method: "POST", body: { api_key: key },
          })
        : await apiJson(path, { token, cacheTtl: 0 });
      if (gen !== fetchGen.current) return;
      const data = res.ok ? res.data : null;
      applyModels(data?.models || [], {
        live: !!data?.live,
        note: data?.note,
        error: data?.error,
        defaultModel: data?.default_model,
      });
    } catch {
      if (gen !== fetchGen.current) return;
      applyModels([], { live: false, note: "Couldn't load models." });
    }
    loadedSpec.current = spec;
    setHasLoaded(true);
    setLoading(false);
  }, [token, disabled, providerId, apiKey, spec, applyModels]);

  function ensureLoaded() {
    if (!hasLoaded || loadedSpec.current !== spec) load();
  }

  // autoSelectFirst keeps one eager load (mount + provider arrival) so the
  // launch form still preselects a model; everything else loads on demand.
  useEffect(() => {
    if (!autoSelectFirst || autoSelected.current || disabled) return;
    if (providerId !== prevProvider.current) {
      prevProvider.current = providerId;
      load();
    }
  }, [autoSelectFirst, disabled, providerId, load]);

  // Eager load on mount whenever a token is available: the picker badge,
  // the parent model catalog (e.g. Composer's provider prefix), and the
  // option list are all populated without requiring a first open.
  useEffect(() => {
    if (disabled || hasLoaded || !token) return;
    load();
  }, [disabled, hasLoaded, token, load]);

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
      ensureLoaded();
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
  const badge = loading ? "Loading…" : !hasLoaded ? "Tap to load" : live ? "Live API" : models.length ? "Catalog" : "No models";

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
          setOpen((v) => {
            if (!v) ensureLoaded();
            return !v;
          });
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
              <li className="empty-state">{note || "No models found — connect a provider in Command Center → AI providers."}</li>
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
