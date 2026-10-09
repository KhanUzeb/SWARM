import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { ChevronDown } from "lucide-react";
import { Lamp } from "../components/Flap";
import { placeOverlay, type OverlayPlacement } from "../lib/overlay";
import { apiJson } from "../lib.js";
import { modelEmptyState, modelFootHint, modelListRequest } from "./modelConnection.js";

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
  const [placement, setPlacement] = useState<OverlayPlacement | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const fetchGen = useRef(0);
  const autoSelected = useRef(false);
  const loadedSpec = useRef("");
  const spec = `${providerId} ${apiKey.trim()}`;

  // Per-instance ids: this picker is mounted in several places at once, so the
  // listbox and each option must not share one hard-coded id (defect 5).
  const uid = useId();
  const listId = `model-list${uid}`;
  const optionId = (index: number) => `${listId}-opt-${index}`;

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
    const request = modelListRequest({ providerId, apiKey });
    const generation = ++fetchGen.current;
    setLoading(true);
    setError("");
    try {
      // The preview and the connect flow both go through modelListRequest, so
      // the key previewed here is provably the key ProviderPanel connects with
      // (defect 1): one builder decides POST-live-preview vs GET-catalog.
      const response = request.method === "POST"
        ? await apiJson(request.path, { token, method: "POST", body: request.body })
        : await apiJson(request.path, { token, cacheTtl: 0 });
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

  // A changed key or provider invalidates the list the picker is showing: the
  // next open (ensureLoaded) re-fetches under the new spec, and a fresh
  // auto-select is allowed so a second instance/provider isn't blocked by the
  // first one's latch.
  useEffect(() => {
    autoSelected.current = false;
  }, [spec]);

  useEffect(() => {
    if (!open) return undefined;
    const onDocumentDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocumentDown);
    window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => document.removeEventListener("mousedown", onDocumentDown);
  }, [open]);

  // An inline picker lives inside a surface someone else already placed.
  const position = useCallback(() => {
    if (inline || !rootRef.current || !popRef.current) return;
    setPlacement(placeOverlay(rootRef.current.getBoundingClientRect(), popRef.current));
  }, [inline]);

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

  useLayoutEffect(() => {
    if (!open) {
      setPlacement(null);
      return undefined;
    }
    position();
    function onReflow() {
      position();
    }
    window.addEventListener("resize", onReflow);
    window.addEventListener("scroll", onReflow, true);
    return () => {
      window.removeEventListener("resize", onReflow);
      window.removeEventListener("scroll", onReflow, false);
    };
    // The list grows as it loads, and the panel rides the viewport edge,
    // so the placement is re-measured whenever its height can have changed.
  }, [open, position, filtered.length]);

  useEffect(() => setActive(0), [open, query, filtered.length]);

  // Keep the ARIA-active option scrolled into view for keyboard users, matching
  // the visual `.active` that a mouse user sees. keyed on the stable list id.
  useEffect(() => {
    if (!open) return;
    document.getElementById(`${listId}-opt-${active}`)?.scrollIntoView({ block: "nearest" });
  }, [open, active, filtered.length, listId]);

  function pick(modelId: string) {
    onChange?.(modelId);
    setQuery("");
    // Close on selection so the committed value shows in the toggle; the tab
    // strip pattern relies on the popover dismissing once a choice lands.
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

  const previewing = Boolean(apiKey.trim()) && Boolean(providerId);
  const customKeyless = providerId === "custom" && !apiKey.trim();

  return (
    <div className="model-picker" ref={rootRef}>
      {/* Inline, the surrounding surface owns the trigger; a second one
          inside the open panel is a control that opens what is already open. */}
      {!inline && (
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
          <span className={`model-badge${live ? " is-live" : ""}`}>
            {live && <Lamp tone="go" />}
            {badge}
          </span>
          <ChevronDown size={12} className="model-picker-chevron" aria-hidden="true" />
        </button>
      )}
      {open && (
        <div
          ref={popRef}
          className={`model-picker-pop${inline ? " model-picker-pop-inline" : ""}`}
          style={
            inline || !placement
              ? undefined
              : { top: placement.top, left: placement.left, maxHeight: placement.maxHeight }
          }
        >
          <input
            ref={inputRef}
            className="model-picker-search"
            value={query}
            placeholder={placeholder}
            onChange={(event) => { setQuery(event.target.value); setOpen(true); }}
            onKeyDown={onKeyDown}
            autoComplete="off"
            spellCheck={false}
            role="combobox"
            aria-label="Search models"
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={open && filtered[active] ? optionId(active) : undefined}
          />
          <ul className="model-picker-list" id={listId} role="listbox" aria-label="Models">
            {loading && !filtered.length && <li className="empty-state">Fetching models from your provider…</li>}
            {!loading && !filtered.length && (
              <li className="empty-state">{modelEmptyState({ note, customKeyless })}</li>
            )}
            {filtered.slice(0, 80).map((model, index) => {
              const provider = providerOf(model);
              return (
                <li
                  key={`${model.provider_id || ""}:${model.id}`}
                  id={optionId(index)}
                  role="option"
                  aria-selected={model.id === value}
                  className={`model-option${index === active ? " active" : ""}${model.id === value ? " selected" : ""}`}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => pick(model.id)}
                >
                  <span className="model-option-name">{labelOf(model)}</span>
                  <span className="model-option-id">{provider ? `${provider} · ` : ""}{model.id}</span>
                </li>
              );
            })}
          </ul>
          <div className="model-picker-foot">
            <span className={`hint${error ? " error" : ""}`}>
              {modelFootHint({ error, note, live, previewing })}
            </span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => void load()} disabled={loading}>
              {loading ? "Refreshing…" : "Refresh"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
