
import { test, expect } from "bun:test";
import {
  buildSteps, pairAuditLines, parseAuditLine, usageTotals, runDuration, diffLines, fmtDuration,
} from "./inspectorModel.ts";
import {
  approvalDetail, approvalShape, runForApproval, sortApprovals, decisionLatency,
} from "./approvalsModel.ts";

// Payloads copied verbatim from backend/main.py _run_agent / persist_tools.
const events = [
  { seq: 1, type: "work_queued", payload: { objective: "ship the release note", source: "chat" }, created_at: 1000 },
  { seq: 2, type: "work_started", payload: { objective: "ship the release note", agent: "swarm" }, created_at: 1001 },
  { seq: 3, type: "agent_started", payload: { agent: "swarm", role: "agent" }, step_id: "swarm", created_at: 1002 },
  { seq: 4, type: "tool_started", payload: { tool: "read_only_shell" }, step_id: "read_only_shell", created_at: 1003 },
  { seq: 5, type: "tool_finished", payload: { tool: "read_only_shell", result: "ok" }, step_id: "read_only_shell", created_at: 1006 },
  { seq: 6, type: "tool_started", payload: { tool: "request_approval" }, step_id: "request_approval", created_at: 1007 },
  { seq: 7, type: "tool_finished", payload: { tool: "request_approval", result: "approval #4 raised" }, step_id: "request_approval", created_at: 1008 },
  { seq: 8, type: "approval_requested", payload: { label: "post the release note", approval_id: 4 }, step_id: "request_approval", created_at: 1009 },
  { seq: 9, type: "message_linked", payload: { message_id: 77, agent: "swarm" }, step_id: "swarm", created_at: 1010 },
];
const audit = [
  parseAuditLine("swarm ran: read_only_shell({'command': 'git log'}) -> ok"),
  parseAuditLine("swarm ran: request_approval({'action': \"post the release note\", 'detail': 'show me the text'}) -> approval #4 raised"),
];

test("audit lines parse into tool, args and result", () => {
  expect(audit[0].tool).toBe("read_only_shell");
  expect(audit[0].args).toBe("{'command': 'git log'}");
  expect(audit[1].args).toContain("action");
});

test("steps fold in order, pairing tool_finished onto its started row", () => {
  const steps = buildSteps(events, audit);
  expect(steps.map(s => s.kind)).toEqual([
    "run", "run", "model", "tool", "tool", "approval", "reply",
  ]);
  const shell = steps[3];
  expect(shell.label).toBe("read_only_shell");
  expect(shell.output).toBe("ok");
  expect(shell.ms).toBe(3000); // 1003 -> 1006
  expect(shell.args).toBe("{'command': 'git log'}"); // paired by order
  expect(steps[5].label).toBe("post the release note");
});

test("a second agent start reads as a handoff, the first as a model call", () => {
  const steps = buildSteps([
    ...events.slice(0, 3),
    { seq: 4, type: "agent_started", payload: { agent: "coder", role: "engineer" }, created_at: 1004 },
  ], []);
  expect(steps[2].kind).toBe("model");
  expect(steps[3].kind).toBe("handoff");
});

test("failure is flagged so the panel opens on it", () => {
  const steps = buildSteps([
    { seq: 1, type: "work_failed", payload: { error: "provider timed out" }, created_at: 10 },
  ], []);
  expect(steps[0].failure).toBe(true);
  expect(steps[0].tone).toBe("hold");
});

test("tool results that read as an error light the lamp red", () => {
  const steps = buildSteps([
    { seq: 1, type: "tool_started", payload: { tool: "curl" }, created_at: 1 },
    { seq: 2, type: "tool_finished", payload: { tool: "curl", result: "connection refused" }, created_at: 2 },
  ], []);
  expect(steps[0].tone).toBe("hold");
});

test("args are withheld when the audit count does not match the tool count", () => {
  const steps = buildSteps(events, [audit[0]]); // one audit line, two tool rows
  expect(steps[3].args).toBeNull();
  expect(steps[4].args).toBeNull();
});

test("args are withheld when audit order drifted", () => {
  const swapped = [audit[1], audit[0]];
  const steps = buildSteps(events, swapped);
  expect(steps[3].args).toBeNull();
});

test("no tokens are ever invented", () => {
  expect(usageTotals(events)).toBeNull();
  expect(usageTotals([{ seq: 1, type: "x", payload: { usage: { prompt_tokens: 12, completion_tokens: 3 } } }]))
    .toEqual({ prompt: 12, completion: 3 });
});

test("duration comes from the session, not from invented wall time", () => {
  const done = { id: "w", created_at: 1000, finished_at: 1030, status: "completed" };
  const live = { id: "w", created_at: 1000, status: "running" };
  const bare = { id: "w", created_at: 1000, status: "queued" };
  expect(runDuration(done, events)).toBe(30000);
  // A live run measures to its last recorded event, never to the wall clock,
  // so the readout is a fact about the log rather than a live counter.
  expect(runDuration(live, events)).toBe(10000);
  expect(runDuration(bare, [])).toBeNull();
  expect(fmtDuration(30000)).toBe("30s");
  expect(fmtDuration(null)).toBe("");
});

test("audit lines attach to the run that produced them", () => {
  const sessions = [
    { id: "w1", channel_id: "general", status: "completed" },
    { id: "w2", channel_id: "general", status: "running" },
  ];
  const eventsByWork = {
    w1: [{ seq: 1, type: "agent_started", payload: { agent: "swarm" } }, { seq: 2, type: "message_linked", payload: { message_id: 20 } }],
    w2: [{ seq: 1, type: "agent_started", payload: { agent: "coder" } }],
  };
  const messages = {
    10: { author_kind: "system", author: "swarm", body: "swarm ran: read_only_shell({'command': 'ls'}) -> a.txt" },
    20: { author_kind: "agent", author: "swarm", body: "done" },
    30: { author_kind: "system", author: "coder", body: "coder ran: write_workspace({'path': 'a.txt'}) -> wrote" },
  };
  const out = pairAuditLines(sessions, eventsByWork, messages, [10, 20, 30], "general");
  expect(out.w1).toHaveLength(1);
  expect(out.w1[0].tool).toBe("read_only_shell");
  expect(out.w2).toHaveLength(1);
  expect(out.w2[0].tool).toBe("write_workspace");
});

test("a channel's audit lines never leak into another channel", () => {
  const sessions = [{ id: "w1", channel_id: "other", status: "completed" }];
  const eventsByWork = { w1: [{ seq: 1, type: "agent_started", payload: { agent: "swarm" } }, { seq: 2, type: "message_linked", payload: { message_id: 20 } }] };
  const messages = { 10: { author_kind: "system", author: "swarm", body: "swarm ran: curl({}) -> x" }, 20: { author_kind: "agent", author: "swarm", body: "done" } };
  expect(pairAuditLines(sessions, eventsByWork, messages, [10, 20], "general")).toEqual({});
});

// ---------------------------------------------------------------- approvals

const approval = {
  id: 4, agent_name: "swarm", channel_id: "general",
  action: "post the v0.1.0 release note to the public status page",
  detail: "Draft the note, then show the text here before anything is published.",
  status: "pending", created_at: 1009, resolved_at: null,
};

test("an outbound action is recognised from the recorded text", () => {
  expect(approvalShape(approval)).toBe("outbound");
  expect(approvalShape({ id: 1, action: "run rm -rf build on the host", detail: "" })).toBe("command");
  expect(approvalShape({ id: 1, action: "edit the migration file", detail: "" })).toBe("write");
  expect(approvalShape({ id: 1, action: "", detail: "" })).toBe("unspecified");
});

test("recorded request_approval arguments are shown as what will run", () => {
  const d = approvalDetail(approval, events, audit);
  expect(d.args.map(a => a.key)).toEqual(["action", "detail"]);
  expect(d.args[0].value).toBe("post the release note");
  expect(d.args[1].value).toBe("show me the text");
});

test("a diff is rendered only when the detail actually contains diff lines", () => {
  expect(diffLines("no diff here")).toEqual([]);
  const withDiff = { id: 5, action: "apply this patch", detail: "diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new" };
  const d = approvalDetail(withDiff, [], []);
  expect(d.diff.length).toBe(4);
  expect(d.hasCommand).toBe(true); // `diff --git …` matches the command shape
});

test("an approval with no detail says so instead of implying safety", () => {
  const bare = { id: 6, action: "do the thing", detail: "", status: "pending" };
  const d = approvalDetail(bare, [], []);
  expect(d.command).toBeNull();
  expect(d.diff).toEqual([]);
});

test("the run is matched by channel, live first, newest before the request", () => {
  const runs = [
    { workId: "w_old", channel_id: "general", status: "completed", created_at: 100 },
    { workId: "w_now", channel_id: "general", status: "waiting_for_approval", created_at: 900 },
    { workId: "w_elsewhere", channel_id: "dm-x", status: "running", created_at: 950 },
  ];
  expect(runForApproval(approval, runs).workId).toBe("w_now");
  expect(runForApproval({ ...approval, channel_id: "unknown" }, runs)).toBeNull();
});

test("pending sorts above decided, newest first", () => {
  const rows = [
    { id: 1, status: "approved", created_at: 500 },
    { id: 2, status: "pending", created_at: 100 },
    { id: 3, status: "denied", created_at: 900 },
    { id: 4, status: "pending", created_at: 800 },
  ];
  expect(sortApprovals(rows).map(r => r.id)).toEqual([4, 2, 3, 1]);
});

test("decision latency is null until a decision is recorded", () => {
  expect(decisionLatency(approval)).toBeNull();
  expect(decisionLatency({ ...approval, status: "approved", resolved_at: 1090 })).toBe(81000);
});