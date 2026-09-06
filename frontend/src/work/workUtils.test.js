import { describe, expect, test } from "bun:test";
import {
  WORK_ACTIVE,
  applyWorkEventToSession,
  groupWorkByAttention,
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
    // Approvals read amber; failures read red.
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
});
