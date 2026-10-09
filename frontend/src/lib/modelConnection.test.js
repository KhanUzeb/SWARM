import { describe, expect, test } from "bun:test";
import {
  MIN_API_KEY_LEN,
  isUsableKey,
  modelListRequest,
  draftAfterSave,
  modelEmptyState,
  modelFootHint,
} from "../ai-support/modelConnection.js";

const src = (rel) => Bun.file(`src/${rel}`).text();

describe("preview and connect share one key gate (defect 1)", () => {
  test("MIN_API_KEY_LEN mirrors the backend preview threshold", () => {
    // backend/ai_support/catalog.py `_auth_from_key` refuses a key shorter
    // than 8; the frontend must draw the exact same line so a key that
    // previews is a key that can connect.
    expect(MIN_API_KEY_LEN).toBe(8);
  });

  test("isUsableKey trims and rejects short keys", () => {
    expect(isUsableKey("        ")).toBe(false);
    expect(isUsableKey("1234567")).toBe(false); // 7 < 8
    expect(isUsableKey("12345678")).toBe(true);
    expect(isUsableKey("  sk-live-abc  ")).toBe(true);
    expect(isUsableKey(undefined)).toBe(false);
    expect(isUsableKey(null)).toBe(false);
  });

  test("a usable key + provider previews the live API with POST, saving nothing", () => {
    const req = modelListRequest({ providerId: "groq", apiKey: "gsk_live_12345678" });
    expect(req).toEqual({
      method: "POST",
      path: "/api/ai-support/providers/groq/models",
      body: { api_key: "gsk_live_12345678" },
    });
  });

  test("no key falls back to the server-resolved catalog (GET), never POSTs a blank", () => {
    expect(modelListRequest({ providerId: "groq", apiKey: "short" })).toEqual({
      method: "GET",
      path: "/api/v2/providers/groq/models",
    });
    expect(modelListRequest({ providerId: "groq" })).toEqual({
      method: "GET",
      path: "/api/v2/providers/groq/models",
    });
  });

  test("no provider lists every connected model", () => {
    expect(modelListRequest({ providerId: "", apiKey: "gsk_live_12345678" })).toEqual({
      method: "GET",
      path: "/api/v2/models/connected",
    });
  });

  test("the request path is url-encoded so a provider id can't break the route", () => {
    const encode = (s) => encodeURIComponent(s);
    expect(modelListRequest({ providerId: "open router", apiKey: "12345678" }, encode).path)
      .toBe("/api/ai-support/providers/open%20router/models");
  });

  test("the POST preview path is gated by the same predicate the connect uses", () => {
    // A key that fails isUsableKey must never take the POST preview branch.
    for (const key of ["", "short", "1234567", undefined]) {
      expect(modelListRequest({ providerId: "groq", apiKey: key }).method).toBe("GET");
    }
    for (const key of ["12345678", "  sk-live-abcdef "]) {
      expect(modelListRequest({ providerId: "groq", apiKey: key }).method).toBe("POST");
    }
  });
});

describe("a save is honest only after it succeeds (defect 3)", () => {
  test("a failed save reverts the draft to what is already active on the provider", () => {
    expect(draftAfterSave({ requested: "gpt-4o", ok: false, serverModel: "gpt-4o-mini" }).model)
      .toBe("gpt-4o-mini");
  });

  test("a failed save does not keep the requested-but-unsaved model selected", () => {
    const next = draftAfterSave({ requested: "gpt-4o", ok: false, serverModel: "" });
    expect(next.model).toBe("");
    expect(next.model).not.toBe("gpt-4o");
  });

  test("a successful save keeps the saved id", () => {
    expect(draftAfterSave({ requested: "gpt-4o", saved: "gpt-4o", ok: true }).model).toBe("gpt-4o");
    expect(draftAfterSave({ requested: "gpt-4o", ok: true }).model).toBe("gpt-4o");
  });
});

describe("empty-state and footer copy are honest and current (defect 4)", () => {
  test("the empty state never names the retired Command Center path", () => {
    for (const state of [{}, { note: "" }, { customKeyless: true }]) {
      expect(modelEmptyState(state)).not.toContain("Command Center");
      expect(modelEmptyState(state)).not.toContain("AI providers");
    }
  });

  test("the generic empty state routes to Connect, the surface that owns providers", () => {
    expect(modelEmptyState({})).toContain("Connect");
  });

  test("a backend note wins as the empty-state text", () => {
    expect(modelEmptyState({ note: "Paste an API key to load this provider's live model list." }))
      .toBe("Paste an API key to load this provider's live model list.");
  });

  test("custom keyless says the id must be typed, not that a list will appear", () => {
    const text = modelEmptyState({ customKeyless: true });
    expect(text.toLowerCase()).toContain("type");
  });

  test("previewing but unreachable never blames a missing key", () => {
    // A key is present, so 'connect a key' advice is a lie — say it couldn't
    // reach the provider instead.
    const hint = modelFootHint({ previewing: true, live: false });
    expect(hint).not.toContain("connect a key");
    expect(hint.toLowerCase()).toContain("catalog");
  });

  test("catalog-without-a-key still invites a key", () => {
    expect(modelFootHint({ previewing: false, live: false })).toContain("connect a key");
  });

  test("live with a typed key is attributed to the user's provider", () => {
    expect(modelFootHint({ previewing: true, live: true })).toContain("your provider");
  });

  test("an error outranks every other footer hint", () => {
    expect(modelFootHint({ error: "boom", note: "quiet", live: true })).toBe("boom");
  });
});

describe("component wiring invariants (source of truth)", () => {
  test("ProviderPanel connects with the shared gate, not a private length check", async () => {
    const panel = await src("ai-support/ProviderPanel.jsx");
    // Defect 1: preview and connect used separate key rules. Both must import
    // and call the one predicate; no inline `key.length < 8` left behind.
    expect(panel).toContain("isUsableKey");
    expect(panel).not.toMatch(/key\.length\s*<\s*8/);
    expect(panel).not.toMatch(/apiKey\.length\s*<\s*8/);
  });

  test("ModelPicker previews via the shared request builder, not an inline gate", async () => {
    const picker = await src("ai-support/ModelPicker.tsx");
    expect(picker).toContain("modelListRequest");
    // The old inline "POST when apiKey >= 8" gate is gone.
    expect(picker).not.toMatch(/length\s*>=\s*8/);
  });

  test("the custom card previews a live list with the shared picker, not just a typed id", async () => {
    const panel = await src("ai-support/ProviderPanel.jsx");
    // Defect 2: the custom endpoint wired no picker at all. It now mounts one.
    expect(panel).toMatch(/<ModelPicker[^>]*providerId="custom"/s);
  });

  test("the picker is a labelled combobox with aria-activedescendant", async () => {
    const picker = await src("ai-support/ModelPicker.tsx");
    // Defect 6: arrow keys moved a visual-only index; the active option must
    // be exposed to AT and the input must be a combobox.
    expect(picker).toContain('role="combobox"');
    expect(picker).toContain("aria-activedescendant");
    expect(picker).toContain("aria-expanded");
    expect(picker).toContain("aria-controls");
  });

  test("list and option ids are per-instance so multiple pickers don't collide", async () => {
    const picker = await src("ai-support/ModelPicker.tsx");
    // Defect 5: ModelPicker is mounted in several places; ids must be unique
    // per instance (useId) rather than the shared `id` prop.
    expect(picker).toContain("useId");
  });

  test("each option carries a stable id for aria-activedescendant to reference", async () => {
    const picker = await src("ai-support/ModelPicker.tsx");
    expect(picker).toContain('role="option"');
    expect(picker).toContain("aria-selected");
    expect(picker).toMatch(/id=\{optionId\(index\)\}/);
    expect(picker).toContain("scrollIntoView");
  });

  test("the picker closes on select and on Escape", async () => {
    const picker = await src("ai-support/ModelPicker.tsx");
    expect(picker).toContain("setOpen(false)");
    expect(picker).toContain('event.key === "Escape"');
  });

  test("changing the key drops the model chosen under the old key", async () => {
    const panel = await src("ai-support/ProviderPanel.jsx");
    // Defect 1 (panel side): editing the key resets the draft model, so a list
    // chosen under key A can never be connected under key B.
    expect(panel).toContain("key: e.target.value, model: \"\"");
  });

  test("a failed save reverts the draft model instead of keeping the unsaved pick", async () => {
    const panel = await src("ai-support/ProviderPanel.jsx");
    // Defect 3 (panel side): the draft resolves through draftAfterSave, which
    // restores the server model on failure and only claims a model on success.
    expect(panel).toContain("draftAfterSave");
    expect(panel).toContain("commit(false)");
  });
});

