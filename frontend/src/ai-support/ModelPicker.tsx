import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { apiJson } from "../lib.js";

export interface ModelOption {
  id: string;
  name?: string;
  provider_id?: string;
  provider_name?: string;
  default?: boolean;
  custom?: boolean;
}

interface ModelPickerProps {
  id?: string;
  token?: string;
  value?: string;
  onChange?: (modelId: string) => void;
  providerId?: string;
  apiKey?: string;
  placeholder?: string;
  disabled?: boolean;
  autoSelectFirst?: boolean;
  openOnMount?: boolean;
  inline?: boolean;
  onModelsLoaded?: (models: ModelOption[], meta?: Record<string, unknown>) => void;
}

function labelOf(model: ModelOption): string {
  if (model.name && model.name !== model.id) return model.name;
  return model.id;
}

function providerOf(model: ModelOption): string {
  return model.provider_name || model.provider_id || "";
}

export default function ModelPicker({
  id,
  token,
  value = "",
  onChange,
  providerId = "",
  apiKey = "",
  placeholder = "Search models",
  disabled = false,
  autoSelectFirst = false,
  openOnMount = false,
  inline = false,
  onModelsLoaded,
}: ModelPickerProps) {
  const [open, setOpen] = useState(openOnMount);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [live, setLive] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [active, setActive] = useState(0);
  const [hasLoaded, setHasLoaded] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const fetchGen = useRef(0);
  const autoSelected = useRef(false);
  const loadedSpec = useRef("");
  const spec = `${providerId} ${apiKey.trim()}`;

  const applyModels = useCallback((list: ModelOption[], meta: Record<string, unknown> = {}) => {
    setModels(list);
    setLive(Boolean(meta.live));
    setNote(String(meta.note || ""));
    setError(String(meta.error || ""));
    onModelsLoaded?.(list, meta);
    if (autoSelectFirst && !autoSelected.current && list.length && !value) {
      const preferred = String(meta.defaultModel || list.find((model) => model.default)?.id || list[0]?.id || "");
      if (preferred) {
        autoSelected.current = true;
        onChange?.(preferred);
      }
    }
  }, [autoSelectFirst, onChange, onModelsLoaded, value]);

  const load = useCallback(async () => {
    if (!token || disabled) return;
    const generation = ++fetchGen.current;
    setLoading(true);
    setError("");
    try {
      const path = providerId
        ? `/api/v2/providers/${encodeURIComponent(providerId)}/models`
        : "/api/v2/models/connected";
      const response = apiKey.trim().length >= 8 && providerId
        ? await apiJson(`/api/ai-support/providers/${encodeURIComponent(providerId)}/models`, {
            token,
            method: "POST",
            body: { api_key: apiKey.trim() },
          })
        : await apiJson(path, { token, cacheTtl: 0 });
      if (generation !== fetchGen.current) return;
      const data = response.ok ? response.data : null;
      applyModels(data?.models || [], {
        live: Boolean(data?.live),
        note: data?.note,
        error: data?.error,
        defaultModel: data?.default_model,
      });
    } catch {
      if (generation !== fetchGen.current) return;
      applyModels([], { live: false, note: "Could not load models. Try Refresh." });
    } finally {
      if (generation === fetchGen.current) {
        loadedSpec.current = spec;
        setHasLoaded(true);
        setLoading(false);
      }
    }
  }, [apiKey, applyModels, disabled, providerId, spec, token]);

  const ensureLoaded = useCallback(() => {
    if (!hasLoaded || loadedSpec.current !== spec) void load();
  }, [hasLoaded, load, spec]);

  useEffect(() => {
    if (disabled || !token || hasLoaded) return;
    void load();
  }, [disabled, hasLoaded, load, token]);

  useEffect(() => {
    if (!open) return undefined;
    const onDocumentDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocumentDown);
    window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => document.removeEventListener("mousedown", onDocumentDown);
  }, [open]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = models.slice();
    if (value && !list.some((model) => model.id === value)) {
      list.unshift({ id: value, name: value, custom: true });
    }
    if (!needle) return list;
    return list.filter((model) => {
      const haystack = `${model.id} ${model.name || ""} ${providerOf(model)}`.toLowerCase();
      return haystack.includes(needle);
    });
  }, [models, query, value]);

  useEffect(() => setActive(0), [open, query, filtered.length]);

  function pick(modelId: string) {
    onChange?.(modelId);
    setQuery("");
    setOpen(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      ensureLoaded();
      setActive((index) => Math.min(index + 1, Math.max(filtered.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (filtered[active]) pick(filtered[active].id);
      else if (query.trim()) pick(query.trim());
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  const current = models.find((model) => model.id === value);
  const badge = loading
    ? "Loading…"
    : !hasLoaded
      ? "Connect a provider"
      : live
        ? "Live API"
        : models.length
          ? "Catalog"
          : "No models";

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
          setOpen((wasOpen) => {
            if (!wasOpen) ensureLoaded();
            return !wasOpen;
          });
        }}
      >
        <span className="model-picker-value">{current ? labelOf(current) : value || placeholder}</span>
        <span className={`model-live${live ? " on" : ""}`}>{badge}</span>
        <span className="model-picker-chevron" aria-hidden="true">⌄</span>
      </button>
      {open && (
        <div className={`model-picker-pop${inline ? " model-picker-pop-inline" : ""}`}>
          <input
            ref={inputRef}
            className="model-picker-search"
            value={query}
            placeholder={placeholder}
            onChange={(event) => { setQuery(event.target.value); setOpen(true); }}
            onKeyDown={onKeyDown}
            autoComplete="off"
            spellCheck={false}
            aria-label="Search models"
          />
          <ul className="model-picker-list" role="listbox" aria-label="Models">
            {loading && !filtered.length && <li className="empty-state">Fetching models from your provider…</li>}
            {!loading && !filtered.length && (
              <li className="empty-state">{note || "No models found. Connect a provider in Command Center → AI providers."}</li>
            )}
            {filtered.slice(0, 80).map((model, index) => {
              const provider = providerOf(model);
              return (
                <li key={`${model.provider_id || ""}:${model.id}`}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={model.id === value}
                    className={`model-option${index === active ? " active" : ""}${model.id === value ? " selected" : ""}`}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => pick(model.id)}
                  >
                    <span className="model-option-name">{labelOf(model)}</span>
                    <span className="model-option-id">{provider ? `${provider} · ` : ""}{model.id}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="model-picker-foot">
            <span className={`hint${error ? " error" : ""}`}>{error || note || (live ? "Fetched from provider API" : "Catalog models — connect a key for live results")}</span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => void load()} disabled={loading}>
              {loading ? "Refreshing…" : "Refresh"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
