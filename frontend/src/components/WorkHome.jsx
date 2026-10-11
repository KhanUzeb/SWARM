import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiJson, EmptyState } from "../ui.jsx";
import { WorkStatusBadge, ProgressBar, FailureState } from "./Status.jsx";
import { ArtifactCard } from "./ArtifactCard.jsx";
import { SmartComposer } from "./SmartComposer.jsx";

/**
 * Work hero surface (plan §3): Needs You queue + active projects, and a
 * detail view with OVERVIEW | ACTIVITY | AGENTS | ARTIFACTS | CONTEXT tabs.
 * Goal → Plan → Current step → Evidence → Decision → Result, raw events
 * stay under Activity.
 */
const TABS = ["Overview", "Activity", "Agents", "Artifacts", "Context"];

export function WorkHome({ token, onOpenRun, flash }) {
  const [runs, setRuns] = useState([]);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");

  const load = useCallback(async (isLive = () => true) => {
    if (!token) return;
    try {
      const res = await apiJson("/api/v2/runs", { token });
      // Defect: a null body was stored straight into `runs`, and the next
      // render's `runs.filter` threw on it.
      if (!isLive()) return;
      if (res.ok) { setRuns(res.data || []); setError(""); }
      else setError(res.data?.detail || "Could not load work");
    } catch (e) {
      if (isLive()) setError(e.message || "Could not load work");
    }
  }, [token]);

  useEffect(() => {
    let alive = true;
    // Defect: the load was never cancelled, so leaving the view mid-flight set
    // state on an unmounted WorkHome.
    void load(() => alive);
    return () => { alive = false; };
  }, [load]);

  async function startWork(text, { mode }) {
    const body = { objective: mode === "ask" ? text : `[${mode}] ${text}`, policy: "supervised" };
    let res;
    try {
      res = await apiJson("/api/v2/runs", { token, method: "POST", body });
    } catch (e) {
      // Defect: a transport failure rejected out of the submit handler and
      // left the brief on screen with nothing said about why.
      flash?.(e.message || "Could not start work", "error");
      return false;
    }
    if (res.ok) {
      flash?.("Work queued", "success");
      load();
      onOpenRun?.(res.data);
      return true;
    }
    flash?.(res.data?.detail || "Could not start work", "error");
    return false;
  }

  const needsYou = runs.filter((r) => r.status === "waiting_for_approval");
  const active = runs.filter((r) => ["queued", "running"].includes(r.status));
  const rest = runs.filter((r) => !["queued", "running", "waiting_for_approval"].includes(r.status));
  const visible = filter === "needs-you" ? needsYou : filter === "active" ? active : filter === "done" ? rest : runs;

  return (
    <section className="work-home" aria-label="Work">
      <header className="page-header">
        <div>
          <h2>Work</h2>
          <p className="muted">Hand a project to your team and watch the work become legible.</p>
        </div>
        <div className="segmented" role="tablist" aria-label="Work filter">
          {[["all", "All"], ["needs-you", `Needs you (${needsYou.length})`], ["active", `Active (${active.length})`], ["done", "Done"]].map(([v, l]) => (
            <button key={v} role="tab" aria-selected={filter === v} className={`segment ${filter === v ? "active" : ""}`} onClick={() => setFilter(v)}>
              {l}
            </button>
          ))}
        </div>
      </header>

      {error && <p className="error-text" role="alert">{error}. <button className="btn btn-ghost btn-sm" onClick={load}>Retry</button></p>}

      {needsYou.length > 0 && filter === "all" && (
        <div className="needs-you" aria-label="Needs you">
          <h3>Needs you ({needsYou.length})</h3>
          {needsYou.map((r) => <WorkCard key={r.id} run={r} hero onOpen={() => onOpenRun?.(r)} token={token} />)}
        </div>
      )}

      <SmartComposer onSend={startWork} />

      <div className="work-grid">
        {visible.length === 0 && <EmptyState title="No work here" message="Describe an outcome above — agents plan, execute and verify." />}
        {visible.map((r) => <WorkCard key={r.id} run={r} onOpen={() => onOpenRun?.(r)} token={token} />)}
      </div>
    </section>
  );
}

function elapsed(run) {
  // Defect: created_at/finished_at arrive as epoch seconds from v2.create_run
  // but as ISO strings from the work tables, and the raw subtraction printed
  // "NaNm NaNs" for the string case. Normalise both before doing arithmetic.
  const toMs = (t) => (t == null ? 0 : (typeof t === "number" ? t * 1000 : Date.parse(t)) || 0);
  const start = toMs(run.started_at || run.created_at);
  if (!start) return "";
  const end = toMs(run.finished_at) || Date.now();
  const s = Math.max(0, Math.floor((end - start) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}m ${String(s % 60).padStart(2, "0")}s`;
}

export function WorkCard({ run, hero, onOpen, token }) {
  const [progress, setProgress] = useState(null);
  useEffect(() => {
    if (!token || !run?.id) return;
    let alive = true;
    apiJson(`/api/v2/runs/${run.id}/progress`, { token })
      // Defect: the progress poll resolved into an unmounted card and set
      // state there; the card list unmounts wholesale on every refresh.
      .then((res) => { if (alive && res.ok) setProgress(res.data); })
      .catch(() => {});
    return () => { alive = false; };
  }, [token, run?.id]);
  const stepsDone = progress?.evidence_count || 0;
  return (
    <article className={`work-card ${hero ? "hero" : ""}`} aria-label={`Work ${run.objective}`}>
      <div className="work-card-top">
        <span className="pulse-dot" aria-hidden />
        <h4 className="truncate">{run.objective}</h4>
        <WorkStatusBadge status={run.status} />
      </div>
      <p className="muted small">
        {elapsed(run)}
        {/* Defect: middle-dot meta string (AGENTS.md 8a). */}
        {progress?.current_step ? `, now on ${progress.current_step}` : ""}
      </p>
      <ProgressBar done={stepsDone} total={Math.max(stepsDone, (progress?.plan || []).length, 1)} />
      <div className="work-card-actions">
        <button className="btn btn-secondary btn-sm" onClick={onOpen}>Open</button>
        {run.status === "waiting_for_approval" && <button className="btn btn-primary btn-sm" onClick={onOpen}>Review</button>}
      </div>
    </article>
  );
}

export function WorkDetail({ run, token, onApprove, onDeny, onStatusChange }) {
  const [tab, setTab] = useState("Overview");
  const [current, setCurrent] = useState(run);
  const [progress, setProgress] = useState(null);
  const [events, setEvents] = useState([]);
  const [artifacts, setArtifacts] = useState([]);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [verifyMsg, setVerifyMsg] = useState("");
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [approvalMsg, setApprovalMsg] = useState("");

  useEffect(() => { setCurrent(run); }, [run?.id]);

  const statusRef = useRef(null);
  statusRef.current = current?.status;

  useEffect(() => {
    if (!token || !run?.id) return;
    let alive = true;
    async function refresh() {
      try {
        const [r, p, e, a] = await Promise.all([
          apiJson(`/api/v2/runs/${run.id}`, { token }),
          apiJson(`/api/v2/runs/${run.id}/progress`, { token }),
          apiJson(`/api/v2/runs/${run.id}/events`, { token }),
          apiJson(`/api/v2/runs/${run.id}/artifacts`, { token }),
        ]);
        if (!alive) return;
        if (r.ok) {
          setCurrent(r.data);
          onStatusChange?.(r.data);
        }
        if (p.ok) setProgress(p.data);
        if (e.ok) setEvents(e.data);
        if (a.ok) setArtifacts(a.data);
      } catch { /* polling continues */ }
    }
    refresh();
    // Defect: the timer read `current.status` from the render that created it
    // and listed it as a dependency, so every status change tore the interval
    // down and fired an extra immediate poll. Read it through a ref instead.
    const timer = setInterval(() => {
      if (["queued", "running", "waiting_for_approval"].includes(statusRef.current)) refresh();
    }, 2500);
    return () => { alive = false; clearInterval(timer); };
  }, [token, run?.id]);

  const agents = useMemo(() => {
    const names = new Set();
    for (const e of events) {
      const a = e.payload?.agent || e.agent;
      if (a) names.add(a);
    }
    return [...names];
  }, [events]);

  const failure = progress?.failures?.[0];
  // Defect: this took the newest `approval_requested` whether or not it had
  // already been answered, so a run that had cleared an earlier gate had its
  // Approve/Deny POST aimed at that settled step_id. v2 keys the resume on
  // `approval_resolved` per step_id (backend/v2.py), so a settled gate can
  // never advance and the run stalls on the gate it is actually held at.
  const pendingGate = newestUnresolvedGate(events);

  async function resolve(decision) {
    if (onApprove && decision === "approved") return onApprove(run);
    if (onDeny && decision === "denied") return onDeny(run);
    const stepId = pendingGate?.step_id;
    if (!stepId) {
      setApprovalMsg("No pending approval step found.");
      return;
    }
    setApprovalBusy(true);
    setApprovalMsg("");
    try {
      const res = await apiJson(`/api/v2/runs/${run.id}/approvals/${stepId}`, {
        method: "POST", token, body: { status: decision },
      });
      if (res.ok) {
        setCurrent(res.data);
        onStatusChange?.(res.data);
        const ev = await apiJson(`/api/v2/runs/${run.id}/events`, { token });
        if (ev.ok) setEvents(ev.data);
      } else {
        setApprovalMsg(res.data?.detail || "Could not resolve approval");
      }
    } catch (e) {
      setApprovalMsg(e.message || "Could not resolve approval");
    } finally {
      setApprovalBusy(false);
    }
  }

  async function retryRun() {
    setVerifyBusy(true);
    setVerifyMsg("");
    try {
      const res = await apiJson(`/api/v2/runs/${run.id}/start`, { method: "POST", token });
      if (res.ok) {
        setCurrent(res.data);
        onStatusChange?.(res.data);
        const ev = await apiJson(`/api/v2/runs/${run.id}/events`, { token });
        if (ev.ok) setEvents(ev.data);
        setVerifyMsg("Run resumed — completed steps are skipped.");
      } else {
        setVerifyMsg(res.data?.detail || "Could not resume run");
      }
    } catch (e) {
      setVerifyMsg(e.message || "Could not resume run");
    } finally {
      setVerifyBusy(false);
    }
  }

  async function verify(passed) {    setVerifyBusy(true);
    setVerifyMsg("");
    try {
      const res = await apiJson(`/api/v2/runs/${run.id}/verify`, {
        method: "POST",
        token,
        body: { passed, checks: ["result-review"], summary: passed ? "Human verified the result." : "Human flagged an issue.", confidence: passed ? 0.9 : 0.2 },
      });
      if (res.ok) {
        // Defect: `res.data.result.confidence` was read unguarded, so any
        // success body without the contract threw and lost the confirmation.
        const confidence = Number(res.data?.result?.confidence);
        setVerifyMsg(Number.isFinite(confidence)
          ? `Verification recorded as ${res.data?.event || "verification"} at ${Math.round(confidence * 100)}% confidence`
          : "Verification recorded");
      } else setVerifyMsg(res.data?.detail || "Verification failed");
    } catch (e) {
      setVerifyMsg(e.message || "Verification failed");
    } finally {
      setVerifyBusy(false);
    }
  }

  return (
    <section className="work-detail" aria-label={`Work detail ${current?.objective}`}>
      <header className="work-detail-head">
        <h3>{current?.objective}</h3>
        <WorkStatusBadge status={current?.status || "queued"} />
      </header>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>

      {tab === "Overview" && (
        <div className="work-overview">
          <PlanList plan={progress?.plan || []} current={progress?.current_step} status={current?.status} />
          {current?.report?.summary && (
            <div className="report-card">
              <h4>Result</h4>
              <p>{current.report.summary}</p>
              <p className="muted small">{current.report.steps?.length || 0} steps, {(current.report.artifacts || []).length} artifacts</p>
            </div>
          )}
          {current?.status === "waiting_for_approval" && (
            <div className="approval-card rich">
              <h4>Needs your approval{pendingGate?.step_id ? ` — gate ${pendingGate.step_id}` : ""}</h4>
              <p className="muted">Review scope, impact and reversibility before deciding.</p>
              <div className="row">
                <button className="btn btn-primary btn-sm" disabled={approvalBusy} onClick={() => resolve("approved")}>Approve</button>
                <button className="btn btn-ghost btn-sm" disabled={approvalBusy} onClick={() => resolve("denied")}>Deny</button>
              </div>
              {approvalMsg && <p className="muted small" role="status">{approvalMsg}</p>}
            </div>
          )}
          {failure && (
            <FailureState
              title="This step failed — here is what survived"
              detail={failure.payload?.error || failure.payload?.message}
              done={(progress?.plan || []).slice(0, 2)}
              missing={[progress?.current_step || "current step"].filter(Boolean)}
              actions={[{ label: "Retry step", variant: "primary", onClick: retryRun }, { label: "View trace", onClick: () => setTab("Activity") }]}
            />
          )}
          <div className="verify-row">
            <button className="btn btn-secondary btn-sm" disabled={verifyBusy} onClick={() => verify(true)}>Mark verified</button>
            <button className="btn btn-ghost btn-sm" disabled={verifyBusy} onClick={() => verify(false)}>Flag issue</button>
            {verifyMsg && <span className="muted small" role="status">{verifyMsg}</span>}
          </div>
        </div>
      )}

      {tab === "Activity" && (
        <ol className="activity-list">
          {events.map((e) => (
            <li key={e.seq ?? e.id} className="activity-row">
              <span className="activity-type">{e.event_type}</span>
              <span className="muted small">{[e.step_id, e.payload?.agent].filter(Boolean).join(", ") || "run"}</span>
            </li>
          ))}
          {events.length === 0 && <EmptyState title="No activity yet" message="Events will stream here as agents work." />}
        </ol>
      )}

      {tab === "Agents" && (
        <div className="agent-grid">
          {agents.length === 0 && <EmptyState title="No agents yet" message="Agents appear once they act on this work." />}
          {agents.map((a) => (
            <div key={a} className="agent-chip-card"><strong>@{a}</strong><span className="muted small"> participant</span></div>
          ))}
        </div>
      )}

      {tab === "Artifacts" && (
        <div className="artifact-grid">
          {artifacts.length === 0 && <EmptyState title="No artifacts" message="Reports, diffs and files land here." />}
          {artifacts.map((a) => <ArtifactCard key={a.id} artifact={a} />)}
        </div>
      )}

      {tab === "Context" && (
        <div className="context-summary">
          <p className="muted">Evidence {progress?.evidence_count || 0}, decisions {(progress?.decisions || []).length}</p>
          <ul>
            {(progress?.decisions || []).map((d, i) => <li key={i} className="small">{d.type}: {JSON.stringify(d.payload).slice(0, 160)}</li>)}
          </ul>
        </div>
      )}
    </section>
  );
}

/**
 * The gate the run is actually held at: the newest `approval_requested` whose
 * step_id has no matching `approval_resolved` yet.
 */
function newestUnresolvedGate(events) {
  const settled = new Set();
  for (const e of events || []) {
    if (e.event_type === "approval_resolved" && e.step_id) settled.add(e.step_id);
  }
  for (let i = (events || []).length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.event_type !== "approval_requested") continue;
    if (!e.step_id || !settled.has(e.step_id)) return e;
  }
  return undefined;
}

function PlanList({ plan, current, status }) {
  if (!plan.length) return <p className="muted">Plan appears here once the run starts.</p>;
  // Defect: with no current_step every step but the last was painted done, so
  // a queued run showed a plan already executed. Only a finished run may say
  // the whole plan is behind us.
  const allDone = !current && status === "completed";
  return (
    <ol className="plan-list">
      {plan.map((step, i) => {
        const done = current ? plan.indexOf(current) > i : allDone;
        return <li key={i} className={done ? "done" : step === current ? "current" : "todo"}>{done ? "✓ " : step === current ? "● " : "○ "}{step}</li>;
      })}
    </ol>
  );
}
