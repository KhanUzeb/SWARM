import { describe, expect, test } from "bun:test";
import {
  WORK_ACTIVE,
  activitySummary,
  apiErrorText,
  applyWorkEventToSession,
  failureSurvivors,
  groupWorkByAttention,
  launchFailureText,
  mergeWorkEvents,
  workStatusLabel,
  workStatusTone,
} from "./workUtils.js";

describe("work rail states", () => {
  test("every lifecycle status has a tone and label", () => {
    for (const s of ["queued", "running", "waiting_for_approval", "completed", "failed", "cancelled"]) {
      expect(workStatusTone(s)).toBeTruthy();
      expect(workStatusLabel(s)).not.toBe("Unknown");
    }
    // Approvals read hold; failures read red. Live work reads go — there is
    // no third hue for "in progress" (8a).
    expect(workStatusTone("waiting_for_approval")).toBe("warning");
    expect(workStatusTone("failed")).toBe("danger");
    expect(workStatusTone("running")).toBe("intelligence");
  });

  test("attention ordering surfaces approvals, then failures, then running", () => {
    const sessions = [
      { id: "c", status: "completed", created_at: 3 },
      { id: "r", status: "running", created_at: 2 },
      { id: "f", status: "failed", created_at: 1 },
      { id: "a", status: "waiting_for_approval", requires_action: true, created_at: 0 },
      { id: "q", status: "queued", created_at: 4 },
    ];
    const ordered = groupWorkByAttention(sessions).map(s => s.id);
    expect(ordered).toEqual(["a", "f", "r", "q", "c"]);
  });
});

describe("event replay", () => {
  test("merge dedupes by seq and stays sorted", () => {
    const merged = mergeWorkEvents(
      [{ seq: 1, type: "work_queued" }, { seq: 2, type: "work_started" }],
      [{ seq: 2, type: "work_started" }, { seq: 3, type: "agent_started" }],
    );
    expect(merged.map(e => e.seq)).toEqual([1, 2, 3]);
  });

  test("reconnect replay from a cursor drops already-seen events", () => {
    const full = [{ seq: 1 }, { seq: 2 }, { seq: 3 }, { seq: 4 }].map(e => ({ ...e, type: "x" }));
    const after = 2;
    const tail = full.filter(e => e.seq > after);
    const merged = mergeWorkEvents(full.slice(0, 3), tail);
    expect(merged.map(e => e.seq)).toEqual([1, 2, 3, 4]);
  });
});

describe("event-driven transitions", () => {
  const base = { id: "work_1", status: "queued", requires_action: false };

  test("approval requested -> needs action; resolved -> running", () => {
    const asked = applyWorkEventToSession(base, { work_id: "work_1", seq: 2, type: "approval_requested", step_id: "gate" });
    expect(asked.status).toBe("waiting_for_approval");
    expect(asked.requires_action).toBe(true);
    expect(asked.active_step).toBe("gate");
    const resumed = applyWorkEventToSession(asked, { work_id: "work_1", seq: 3, type: "approval_resolved" });
    expect(resumed.status).toBe("running");
    expect(resumed.requires_action).toBe(false);
  });

  test("terminal transitions set the right attention flags", () => {
    expect(applyWorkEventToSession(base, { work_id: "work_1", seq: 1, type: "work_completed" }).requires_action).toBe(false);
    const failed = applyWorkEventToSession(base, { work_id: "work_1", seq: 1, type: "work_failed" });
    expect(failed.status).toBe("failed");
    expect(failed.requires_action).toBe(true);
    expect(applyWorkEventToSession(base, { work_id: "work_1", seq: 1, type: "work_cancelled" }).status).toBe("cancelled");
  });

  test("foreign work ids are ignored", () => {
    expect(applyWorkEventToSession(base, { work_id: "work_9", seq: 1, type: "work_completed" })).toBe(base);
  });

  test("active set matches the rail filter", () => {
    expect(WORK_ACTIVE.has("running")).toBe(true);
    expect(WORK_ACTIVE.has("completed")).toBe(false);
  });

  test("start events move a session to running", () => {
    const started = applyWorkEventToSession(base, { work_id: "work_1", seq: 1, type: "work_started" });
    expect(started.status).toBe("running");
    const agentStarted = applyWorkEventToSession(base, { work_id: "work_1", seq: 2, type: "agent_started", step_id: "swarm" });
    expect(agentStarted.status).toBe("running");
    expect(agentStarted.active_step).toBe("swarm");
  });

  test("replayed events merge without duplicates", () => {
    const first = [{ seq: 1, type: "work_started" }, { seq: 2, type: "agent_started" }];
    const replay = [{ seq: 2, type: "agent_started" }, { seq: 3, type: "work_completed" }];
    const merged = mergeWorkEvents(first, replay);
    expect(merged.map(e => e.seq)).toEqual([1, 2, 3]);
  });
});

describe("launch failure text", () => {
  test("a string detail is used verbatim in the printed alert", () => {
    expect(apiErrorText("no provider connected")).toBe("no provider connected");
    expect(launchFailureText("no provider connected")).toBe(
      "no provider connected. Your brief is still here — try again.",
    );
  });

  test("a validation array is readable and never interpolates as object", () => {
    const detail = [
      { type: "missing", loc: ["body", "objective"], msg: "Field required" },
      { type: "string_type", loc: ["body", "policy"], msg: "Input should be a string" },
    ];
    const text = apiErrorText(detail);
    expect(text).toBe("body.objective: Field required; body.policy: Input should be a string");
    expect(text).not.toContain("[object Object]");
    expect(launchFailureText(detail)).toContain("body.objective: Field required");
  });

  test("object details and empty bodies fall back safely, with a bounded tail", () => {
    expect(apiErrorText({ message: "invalid workflow graph", errors: [1, 2] })).toBe("invalid workflow graph");
    expect(apiErrorText(null)).toBe("Could not start work");
    expect(apiErrorText([])).toBe("Could not start work");
    const many = Array.from({ length: 6 }, (_, i) => ({ msg: `error ${i}` }));
    expect(apiErrorText(many)).toBe("error 0; error 1; error 2 (+3 more)");
    expect(launchFailureText(undefined)).toBe(
      "Could not start work. Your brief is still here — try again.",
    );
  });
});

describe("activity lines", () => {
  test("a failed step names its real error, not just an event type", () => {
    expect(activitySummary({
      event_type: "step_failed", step_id: "code",
      payload: { error: "Agent coder reported an error: timeout" },
    })).toEqual({ label: "Step failed", detail: "Agent coder reported an error: timeout" });
    expect(activitySummary({
      event_type: "run_failed", payload: { error: "unsupported node type" },
    })).toEqual({ label: "Failed", detail: "unsupported node type" });
  });

  test("completed work shows its outcome and cancellations show their reason", () => {
    expect(activitySummary({
      event_type: "step_completed", step_id: "code",
      payload: { label: "Code", output: "wrote three files" },
    })).toEqual({ label: "Step finished", detail: "wrote three files" });
    expect(activitySummary({
      event_type: "run_cancelled", payload: { reason: "owner cancelled" },
    })).toEqual({ label: "Cancelled", detail: "owner cancelled" });
  });

  test("an undecided gate and an unknown event stay readable and bounded", () => {
    expect(activitySummary({
      event_type: "approval_requested", step_id: "gate",
      payload: { label: "Review this plan" },
    })).toEqual({ label: "Needs you", detail: "Review this plan" });
    expect(activitySummary({ event_type: "brand_new_event", step_id: "s1" }))
      .toEqual({ label: "brand new event", detail: "s1" });
    const long = { event_type: "step_completed", payload: { output: "x".repeat(500) } };
    expect(activitySummary(long).detail.length).toBe(180);
  });
});

describe("failure card evidence", () => {
  test("only steps with a completed event are reported as done", () => {
    const plan = ["plan", "code", "review", "report"];
    const evidence = [
      { type: "step_started", step_id: "plan" },
      { type: "step_completed", step_id: "plan", payload: { label: "Plan" } },
      { type: "step_completed", step_id: "code", payload: { label: "Code" } },
      { type: "step_failed", step_id: "review", payload: { error: "agent error" } },
    ];
    expect(failureSurvivors(plan, evidence, "review")).toEqual({
      done: ["plan", "code"],
      missing: ["review", "report"],
    });
  });

  test("nothing completed is never shown as completed", () => {
    expect(failureSurvivors(["plan", "code"], [], "plan")).toEqual({
      done: [], missing: ["plan", "code"],
    });
  });

  test("a failed first step does not credit the second plan entry", () => {
    expect(failureSurvivors(["plan", "code"], [{ type: "step_started", step_id: "plan" }], "plan")).toEqual({
      done: [], missing: ["plan", "code"],
    });
  });

  test("with no plan at all the current step is the only named gap", () => {
    expect(failureSurvivors([], [], "lead")).toEqual({ done: [], missing: ["lead"] });
    expect(failureSurvivors([], [], null)).toEqual({ done: [], missing: [] });
  });
});
