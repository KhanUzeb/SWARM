import { describe, expect, test } from "bun:test";
import {
  WS_MESSAGE_FRAME_TYPES,
  WS_DELTA_FRAME_TYPES,
  WS_APPROVAL_FRAME_TYPES,
  WS_ROSTER_FRAME_TYPES,
  shouldFlushOutbox,
} from "./wsFrames.js";
import {
  badgeClass,
  avatarKindClass,
  quickCreatePayload,
  canSubmitDraft,
} from "../lib.js";
import { EMPTY_STATE_KINDS } from "../ui.jsx";

const src = (rel) => Bun.file(`src/${rel}`).text();

describe("websocket frame contract (backend/main.py hub broadcasts)", () => {
  test("full message frames merge into the log", () => {
    expect(WS_MESSAGE_FRAME_TYPES.has("message")).toBe(true);
    expect(WS_MESSAGE_FRAME_TYPES.has("live")).toBe(true);
  });

  test("reaction removals and deletions merge as deltas, not dropped", () => {
    // api_remove_reaction broadcasts "reaction_removed"; the socket handler
    // used to route only "message_deleted" | "reaction", so an un-react
    // stayed visible until a full reload.
    for (const t of ["message_deleted", "reaction", "reaction_removed"]) {
      expect(WS_DELTA_FRAME_TYPES.has(t)).toBe(true);
    }
  });

  test("approval decisions reload the inbox and the rail", () => {
    // api_resolve_approval broadcasts "approval" to all; ignoring it left
    // the approvals count and work rail stale after deciding elsewhere.
    expect(WS_APPROVAL_FRAME_TYPES.has("approval")).toBe(true);
  });

  test("roster frames reload agents (and channels for archives)", () => {
    // "bot_status" fires on approval resolve / status change, "bot_archived"
    // on delete; only "agents_changed" used to be handled, so flaps lied.
    for (const t of ["agents_changed", "bot_status", "bot_archived"]) {
      expect(WS_ROSTER_FRAME_TYPES.has(t)).toBe(true);
    }
  });

  test("routing sets are disjoint", () => {
    const all = [
      ...WS_MESSAGE_FRAME_TYPES,
      ...WS_DELTA_FRAME_TYPES,
      ...WS_APPROVAL_FRAME_TYPES,
      ...WS_ROSTER_FRAME_TYPES,
    ];
    expect(new Set(all).size).toBe(all.length);
  });
});

describe("registration access contract", () => {
  test("the setup form sends bootstrap and invite credentials to the matching API fields", async () => {
    const app = await src("App.jsx");
    const login = await src("components/LoginScreen.tsx");
    expect(app).toContain("setup_token: setupAccess.value");
    expect(app).toContain("invite: setupAccess.value");
    expect(login).toContain("Remote setup or account invite");
    expect(login).toContain("at least 8 characters if set");
  });
});

describe("outbox drain decision", () => {
  test("drains only over a live socket with queued entries", () => {
    expect(shouldFlushOutbox("connected", 2)).toBe(true);
    expect(shouldFlushOutbox("connected", 0)).toBe(false);
    expect(shouldFlushOutbox("connecting", 2)).toBe(false);
    expect(shouldFlushOutbox("offline", 2)).toBe(false);
  });

  test("the flush effect watches the queue, not just reconnects", async () => {
    // Regression for messages queued while already connected (HTTP outage
    // with the socket up): the effect used to depend on wsStatus alone, so
    // the queue sat until the next reconnect. The effect must list the
    // outbox among its dependencies.
    const app = await src("App.jsx");
    expect(app).toContain("shouldFlushOutbox");
    expect(app).toContain("[wsStatus, outbox, channel, outboxRetryTick]");
    // A 429 or outage must schedule another drain instead of stranding the
    // queue until the user types again.
    expect(app).toContain("retryLater(");
  });
});

describe("badge and avatar classes exist in styles.css", () => {
  test("every badge variant resolves to a defined class or nothing", async () => {
    const css = await src("styles.css");
    for (const v of ["neutral", "subtle", "brand", "success", "warning", "error", "danger", "intelligence", "nope"]) {
      const cls = badgeClass(v);
      expect(cls).not.toContain("undefined");
      if (cls) expect(css).toContain(`.${cls} `);
    }
    expect(badgeClass("success")).toBe("badge-go");
    // Two signal hues only (8a): needs-you is hold, live is go. badge-amber
    // was the third hue and no longer exists in styles.css.
    expect(badgeClass("warning")).toBe("badge-hold");
    expect(badgeClass("error")).toBe("badge-hold");
    // Work-rail tones used to render as `badge undefined`.
    expect(badgeClass("danger")).toBe("badge-hold");
    expect(badgeClass("intelligence")).toBe("badge-go");
  });

  test("avatar kinds resolve to the machined-square classes", async () => {
    const css = await src("styles.css");
    expect(avatarKindClass("human")).toBe("avatar-user");
    expect(avatarKindClass("agent")).toBe("avatar-agent");
    expect(avatarKindClass("system")).toBe("avatar-system");
    expect(avatarKindClass("unknown")).toBe("avatar-user");
    for (const cls of ["avatar-user", "avatar-agent", "avatar-system"]) {
      expect(css).toContain(`.${cls} `);
    }
  });
});

describe("the two-signal-hue rule (AGENTS.md 8a)", () => {
  test("no third signal hue survives anywhere", async () => {
    // Exactly two signal hues exist: --go (live/cleared) and --hold
    // (needs-you). Amber was a third one. Neither the tokens nor the tone
    // classes nor the components may name it again.
    const css = await src("styles.css");
    expect(css).not.toContain("--lamp-hold");
    expect(css).not.toContain(".chip-amber");
    expect(css).not.toContain(".flap-amber");
    expect(css).not.toContain(".badge-amber");
    expect(css).not.toContain(".reg-amber");
    expect(css).not.toContain(".is-amber");
    for (const f of [
      "components/Flap.tsx",
      "components/TopBar.tsx",
      "components/Composer.tsx",
      "components/CommandCenter.jsx",
      "components/MessageList.tsx",
      "components/Sidebar.tsx",
      "components/Status.jsx",
      "work/inspectorModel.ts",
      "work/approvalsModel.ts",
      "App.jsx",
      "lib.js",
      "components/ui/badge.tsx",
    ]) {
      const text = await src(f);
      // Comments may name amber to document the defect that was removed; the
      // rule is about the interface, not the prose about it.
      const lines = text.split("\n").filter(l => !l.trimStart().startsWith("//") && !l.trimStart().startsWith("*") && !l.trimStart().startsWith("/*"));
      expect(lines.filter(l => l.includes("amber") || l.includes("lamp-hold")).join("\n")).toBe("");
    }
  });

  test("every badge variant resolves to a class styles.css defines", async () => {
    const css = await src("styles.css");
    for (const v of ["neutral", "subtle", "brand", "success", "warning", "error", "danger", "intelligence", "nope"]) {
      const cls = badgeClass(v);
      expect(cls).not.toContain("undefined");
      if (cls) expect(css).toContain(`.${cls} {`);
    }
  });

  test("status dots map to the three tones that exist", async () => {
    // StatusDot emitted .status-running / .status-warning / .status-danger,
    // none of which are defined, so every work status dot was invisible.
    const status = await src("components/Status.jsx");
    expect(status).not.toContain("status-${");
    const css = await src("styles.css");
    for (const cls of ["status-dot.go", "status-dot.hold", "status-dot.idle"]) {
      expect(css).toContain(`.${cls} `);
    }
  });

  test("no dead text-mono-xs / text-subtle classes remain", async () => {
    // Neither class is defined in styles.css, so both were no-ops that read
    // as styling while doing nothing.
    const files = [
      "App.jsx", "ui.jsx", "components/ComputerPanel.jsx",
      "components/CommandCenter.jsx", "components/Status.jsx",
    ];
    for (const f of files) {
      const text = await src(f);
      expect(text).not.toContain("text-mono-xs");
      expect(text).not.toContain("text-subtle");
    }
  });

  test("no middle-dot meta strings in the interface", async () => {
    // 8a forbids them outright. Comments in styles.css and the RunInspector
    // docblock are prose, not interface copy, so they are not counted here.
    for (const f of [
      "App.jsx", "components/ComputerPanel.jsx", "components/Composer.tsx",
      "components/CommandCenter.jsx", "components/WorkRail.jsx",
      "components/WorkHome.jsx", "components/ArtifactCard.jsx",
      "components/ContextDrawer.jsx", "components/LoginScreen.tsx",
      "ai-support/AppsPanel.jsx", "ai-support/ModelPicker.tsx",
      "ai-support/ProviderPanel.jsx",
    ]) {
      const text = await src(f);
      const lines = text.split("\n").filter(l => !l.trimStart().startsWith("//") && !l.trimStart().startsWith("*"));
      expect(lines.filter(l => l.includes("·")).join("\n")).toBe("");
    }
  });
});

describe("quick-create payload contract", () => {
  test("each action hits the right endpoint with the right fields", () => {
    expect(quickCreatePayload("channel", { name: "  Room " })).toEqual({
      path: "/api/channels", body: { name: "Room" },
    });
    expect(quickCreatePayload("dm", { name: "alice" })).toEqual({
      path: "/api/dms", body: { handle: "alice" },
    });
    expect(quickCreatePayload("group", { name: "g", detail: "t", members: ["swarm"] })).toEqual({
      path: "/api/channels",
      body: { name: "g", topic: "t", kind: "group", members: ["swarm"] },
    });
    expect(quickCreatePayload("team", { name: "t", detail: "d", members: ["swarm"] })).toEqual({
      path: "/api/teams",
      body: { name: "t", description: "d", members: ["swarm"] },
    });
    const agent = quickCreatePayload("agent", {
      name: "coder", detail: "", templates: [{ id: "x", job: "Engineer" }], selectedTemplate: "x",
    });
    expect(agent.path).toBe("/api/agents");
    expect(agent.body.job).toBe("Engineer");
    expect(agent.body.system_prompt).toContain("coder");
  });

  test("incomplete forms are refused with an error, not sent", () => {
    expect(quickCreatePayload("channel", { name: "  " }).error).toBeTruthy();
    expect(quickCreatePayload("group", { name: "g", members: [] }).error).toBeTruthy();
    expect(quickCreatePayload("team", { name: "t", members: [] }).error).toBeTruthy();
  });

  test("create failures surface instead of vanishing", async () => {
    // The dialog used to swallow !res.ok and network throws (and a throw
    // left the Create button stuck busy via a missing finally).
    const app = await src("App.jsx");
    expect(app).toContain('role="alert">{error}');
    expect(app).toContain("finally {");
  });
});

describe("composer submit guard", () => {
  test("needs text, and refuses double-sends", () => {
    expect(canSubmitDraft({ text: " hello ", busy: false, disabled: false })).toBe(true);
    expect(canSubmitDraft({ text: "   ", busy: false, disabled: false })).toBe(false);
    expect(canSubmitDraft({ text: "hi", busy: true, disabled: false })).toBe(false);
    expect(canSubmitDraft({ text: "hi", busy: false, disabled: true })).toBe(false);
  });

  test("SmartComposer uses the guard and labels Enter honestly", async () => {
    const composer = await src("components/SmartComposer.jsx");
    expect(composer).toContain("canSubmitDraft");
    expect(composer).toContain("setSending(true)");
    expect(composer).toContain("Enter sends");
    expect(composer).not.toContain("Ctrl + Enter sends");
  });
});

describe("dead styling-system classes", () => {
  test("no space-separated btn ghost/primary classes remain", async () => {
    // styles.css only defines .btn-ghost / .btn-primary; `btn ghost`
    // rendered as a bare .btn. Every occurrence now uses the hyphenated form.
    for (const f of [
      "ai-support/AppsPanel.jsx",
      "ai-support/PluginsPanel.jsx",
      "ai-support/ProviderPanel.jsx",
      "ai-support/BrowserPanel.jsx",
      "ai-support/SystemPanel.jsx",
      "ai-support/ToolsPanel.jsx",
      "components/ComputerPanel.jsx",
    ]) {
      const text = await src(f);
      expect(text).not.toContain('"btn ghost');
      expect(text).not.toContain('"btn primary');
    }
  });

  test("empty-state kinds are all backed by an icon", () => {
    expect(EMPTY_STATE_KINDS).toContain("search");
    expect(EMPTY_STATE_KINDS).not.toContain("docs");
  });

  test("knowledge empty state uses a backed kind", async () => {
    const view = await src("components/KnowledgeView.jsx");
    expect(view).not.toContain('kind="docs"');
  });
});

describe("previously crashing icon references are imported", () => {
  test("ComputerPanel imports the icons it renders", async () => {
    // PanelChrome rendered <X> and file rows rendered <FileText> with no
    // import — a ReferenceError the moment the panel opened.
    const panel = await src("components/ComputerPanel.jsx");
    expect(panel).toMatch(/import\s*\{[^}]*\bX\b[^}]*\bFileText\b[^}]*\}\s*from\s*"lucide-react"/);
  });

  test("App files view imports the icons it renders", async () => {
    const app = await src("App.jsx");
    expect(app).toMatch(/import\s*\{[^}]*\bFolder\b[^}]*\bFileText\b[^}]*\}\s*from\s*"lucide-react"/);
  });
});

describe("places and connect are first-class destinations", () => {
  test("sidebar exposes Places and Connect in their own group with aria-current", async () => {
    // The slide-over held every one of these surfaces with no path from
    // first paint: PRIMARY_NAV never listed them and neither did the top
    // tabs. They get their own disclosure group, not six more primary rows.
    const sidebar = await src("components/Sidebar.tsx");
    expect(sidebar).toContain("WORKSPACE_NAV");
    expect(sidebar).toContain('aria-label="Workspace"');
    expect(sidebar).toContain("aria-current");
    // The destination names and ids are owned by PANEL_GROUPS: the roster
    // offers exactly the groups the module opens, and never a tab copy.
    expect(sidebar).toContain("PANEL_GROUPS.map(");
    expect(sidebar).toContain("places: Folder");
    expect(sidebar).toContain("connect: Plug");
    expect(sidebar).not.toContain("Sandbox");
  });

  test("top bar view strip includes Places and Connect", async () => {
    const topbar = await src("components/TopBar.tsx");
    expect(topbar).toContain('["home", "Board"]');
    expect(topbar).toContain("PANEL_GROUPS.map(");
    expect(topbar).toContain("aria-current");
    // Destinations, so the strip is a labelled group of buttons. A tablist
    // promises arrow-key navigation this strip does not implement, and the
    // Sidebar already marks these same destinations with aria-current.
    expect(topbar).not.toContain('role="tab"');
    expect(topbar).toContain('aria-label="Views"');
  });

  test("App mounts one ComputerPanel as the places/connect destination", async () => {
    const app = await src("App.jsx");
    expect(app).toContain('["places", "connect"].includes(mainView)');
    expect(app).toContain("group={mainView}");
    expect(app).not.toContain("computerOpen");
    expect(app).not.toContain("cmd-toggle-computer");
    // Exactly one live mount site for the module: the slide-over is gone,
    // so two copies of one panel can never be live at once.
    expect(app.split("<ComputerPanel").length - 1).toBe(1);
    expect(app).toContain("cmd-view-places");
    expect(app).toContain("cmd-view-connect");
  });

  test("the module renders as a destination, never as a slide-over again", async () => {
    const panel = await src("components/ComputerPanel.jsx");
    expect(panel).toContain('className="computer-embedded"');
    expect(panel).not.toContain("<aside");
    expect(panel).not.toContain("computer-panel");
    // No close affordance and no group strip: the group is chosen by the
    // destination the user arrived at, so nothing here competes with the nav.
    expect(panel).not.toContain("onClose");
    expect(panel).not.toContain("panel-group-tab");
  });

  test("PANEL_GROUPS stays the single owner of the destination and tab list", async () => {
    // App, Sidebar and TopBar navigate to a group id and keep no copy of it.
    const panel = await src("components/ComputerPanel.jsx");
    expect(panel).toContain("export const PANEL_GROUPS");
    const app = await src("App.jsx");
    expect(app).not.toContain("PLACES_TABS");
    // The sandbox tab is labelled the way the product and /api/computer
    // label it: "the sandbox (Sandbox tab)".
    expect(panel).toContain('{ id: "files", label: "Sandbox" }');
    // No surface spells a tab name: they all navigate to a group id.
    for (const f of ["App.jsx", "components/Sidebar.tsx", "components/TopBar.tsx", "components/CommandCenter.jsx"]) {
      const text = await src(f);
      for (const tab of ["Sandbox", "System tab", "Browser tab", "Plugins tab"]) {
        expect(text).not.toContain(tab);
      }
    }
  });

  test("every surface in a group mounts a real panel, with no placeholder", async () => {
    const panel = await src("components/ComputerPanel.jsx");
    for (const [tab, component] of [
      ["files", "SandboxView"], ["system", "SystemPanel"], ["browser", "BrowserPanel"],
      ["ai", "ProviderPanel"], ["apps", "AppsPanel"], ["tools", "ToolsPanel"], ["plugins", "PluginsPanel"],
    ]) {
      expect(panel).toContain(`activeTab === "${tab}" && <${component}`);
    }
  });

  test("every panel module has exactly one live mount site", async () => {
    // One live copy per panel across the whole frontend: a second mount
    // would fetch the same endpoints twice and drift from the first.
    const sources = [];
    for await (const rel of new Bun.Glob("src/**/*.{js,jsx,ts,tsx}").scan(".")) {
      const file = rel.replaceAll("\\", "/");
      if (file === "src/lib/frontendContracts.test.js") continue;
      sources.push([file, await Bun.file(rel).text()]);
    }
    for (const name of ["ProviderPanel", "AppsPanel", "ToolsPanel", "PluginsPanel", "SystemPanel", "BrowserPanel", "SandboxView"]) {
      const mounts = sources.filter(([, text]) => text.includes(`<${name}`)).map(([file]) => file);
      expect(mounts).toEqual(["src/components/ComputerPanel.jsx"]);
    }
    const moduleMounts = sources.filter(([, text]) => text.includes("<ComputerPanel")).map(([file]) => file);
    expect(moduleMounts).toEqual(["src/App.jsx"]);
  });

  test("panel modules read real endpoints", async () => {
    for (const f of ["ProviderPanel", "AppsPanel", "ToolsPanel", "PluginsPanel", "SystemPanel", "BrowserPanel"]) {
      const text = await src(`ai-support/${f}.jsx`);
      expect(text).toContain('"/api');
    }
  });

  test("CommandCenter links to Connect instead of mounting a second ProviderPanel", async () => {
    // Connect owns provider connections now; the board keeps the readiness
    // signal (same fetch the launch form already needs) plus a link.
    const cc = await src("components/CommandCenter.jsx");
    expect(cc).not.toContain("ProviderPanel");
    expect(cc).toContain("onOpenConnect");
    expect(cc).toContain("Open Connect");
  });

  test("destination tab strips stay native buttons that never swallow keys", async () => {
    // Tab and arrow keys must keep working: the strips are plain buttons
    // in a labelled group, with the active one exposed, and no key handler.
    const panel = await src("components/ComputerPanel.jsx");
    expect(panel).toContain('role="group"');
    expect(panel).toContain("aria-current");
    expect(panel).not.toContain("onKeyDown");
  });
});

describe("outbox retry keeps every queued message recoverable", () => {
  test("the queue ref advances synchronously, so a chained drain cannot re-send", async () => {
    // A drain drops its last entry and returns without yielding, and the next
    // chained drain reads outboxRef before React commits the setState — so a
    // ref refreshed only during render POSTed that entry a second time.
    const app = await src("App.jsx");
    expect(app).toContain("next(outboxRef.current)");
    expect(app).toContain("outboxRef.current = value");
  });

  test("a permanent rejection surfaces one text and leaves the rest queued", async () => {
    // Dropping every 4xx while overwriting one banner slot destroyed every
    // queued text except the last (a deleted room 404s the whole queue).
    const app = await src("App.jsx");
    const start = app.indexOf("A permanent 4xx");
    expect(start).toBeGreaterThan(-1);
    const block = app.slice(start, app.indexOf("loadContextStats(channelId);", start));
    expect(block).toContain("drop(entry);");
    expect(block).toContain("return;");
    expect(block).not.toContain("continue;");
    // 408 is the gateway giving up on a slow request, not a rejected message.
    expect(app).toContain("res.status === 408");
  });

  test("a retry goes back to the room the text was typed in", async () => {
    // sendMessage read only the live channel, so a failure that surfaced after
    // a room switch was retried into whichever room was open at the time.
    const app = await src("App.jsx");
    expect(app).toContain("const channelId = (opts && opts.channelId) || channelRef.current;");
    expect(app).toContain("sendFailure.channelId === channel");
    expect(app).toContain("remember(res.data, channelId)");
  });

  test("a drain that outlives its session stops posting", async () => {
    // A drain sleeping between entries could resume after sign-out and post
    // under the next session's token.
    const app = await src("App.jsx");
    expect(app).toContain("const sessionToken = tokenRef.current;");
    expect(app).toContain("if (tokenRef.current !== sessionToken) return;");
  });
});

describe("transcript and composer accessibility", () => {
  test("the stream row is inert, not aria-hidden over focusable markdown", async () => {
    // RichBody emits real links, so aria-hidden left focusable children that
    // announced nothing (axe aria-hidden-focus) and killed the "is writing"
    // note rendered inside it.
    const list = await src("components/MessageList.tsx");
    expect(list).toContain("inert={true}");
    expect(list).toContain("still writing");
  });

  test("the log is one scrollport with one name, and history loads silently", async () => {
    const list = await src("components/MessageList.tsx");
    // No `.log-scroll` rule exists in styles.css, so the class was a no-op.
    expect(list).not.toContain("log-scroll");
    expect(list).toContain('aria-label={channelId ? "Channel transcript" : "Thread transcript"}');
    expect(list).toContain('aria-live={loadingMore ? "off" : "polite"}');
  });

  test("the combobox only claims a popup that is rendered", async () => {
    const composer = await src("components/Composer.tsx");
    // slash.open goes true for any unfinished "/word" while the command list
    // renders only on a match, so aria-expanded pointed at a missing popup.
    expect(composer).toContain("const commandOpen = slash.open && slashItems().length > 0;");
    expect(composer).not.toContain("aria-expanded={mention.open || slash.open}");
    expect(composer).toContain("setSlash({ open: false, index: 0 });");
  });
});
