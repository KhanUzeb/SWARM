import { useEffect, useMemo, useState } from "react";
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

  async function load() {
    if (!token) return;
    try {
      const res = await apiJson("/api/v2/runs", { token });
      if (res.ok) setRuns(res.data);
      else setError(res.data?.detail || "Could not load work");
    } catch (e) {
      setError(e.message || "Could not load work");
    }
  }

  useEffect(() => { load(); }, [token]);

  async function startWork(text, { mode }) {
    const body = { objective: mode === "ask" ? text : `[${mode}] ${text}`, policy: "supervised" };
    const res = await apiJson("/api/v2/runs", { token, method: "POST", body });
    if (res.ok) {
      flash?.("Work queued", "success");
      load();
      onOpenRun?.(res.data);
    } else {
      flash?.(res.data?.detail || "Could not start work", "error");
    }
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

      {error && <p className="error-text" role="alert">{error}. <button className="btn btn-ghost btn-sm" onClick={() => window.location.reload()}>Retry</button></p>}

      {needsYou.length > 0 && filter === "all" && (
        <div className="needs-you" aria-label="Needs you">
          <h3>Needs you ({needsYou.length})</h3>
          {needsYou.map((r) => <WorkCard key={r.id} run={r} hero onOpen={() => onOpenRun?.(r)} token={token} />)}
        </div>
      )}

      <SmartComposer onSend={startWork} />

      <div className="work-grid">
        {visible.length === 0 && <EmptyState title="No work here" hint="Describe an outcome above — agents plan, execute and verify." />}
        {visible.map((r) => <WorkCard key={r.id} run={r} onOpen={() => onOpenRun?.(r)} token={token} />)}
      </div>
    </section>
  );
}

function elapsed(run) {
  const start = run.started_at || run.created_at;
  const end = run.finished_at || Date.now() / 1000;
  if (!start) return "";
  const s = Math.max(0, Math.floor(end - start));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}m ${String(s % 60).padStart(2, "0")}s`;
}

export function WorkCard({ run, hero, onOpen, token }) {
  const [progress, setProgress] = useState(null);
  useEffect(() => {
    if (!token || !run?.id) return;
    apiJson(`/api/v2/runs/${run.id}/progress`, { token })
      .then((res) => { if (res.ok) setProgress(res.data); })
      .catch(() => {});
  }, [token, run?.id]);
  const stepsDone = progress?.evidence_count || 0;
  return (
    <article className={`work-card ${hero ? "hero" : ""}`} aria-label={`Work ${run.objective}`}>
      <div className="work-card-top">
        <span className="pulse-dot" aria-hidden />
        <h4 className="truncate">{run.objective}</h4>
        <WorkStatusBadge status={run.status} />
      </div>
      <p className="muted small">{elapsed(run)} {progress?.current_step ? `· now: ${progress.current_step}` : ""}</p>
      <ProgressBar done={stepsDone} total={Math.max(stepsDone, (progress?.plan || []).length, 1)} />
      <div className="work-card-actions">
        <button className="btn btn-secondary btn-sm" onClick={onOpen}>Open</button>
        {run.status === "waiting_for_approval" && <button className="btn btn-primary btn-sm" onClick={onOpen}>Review</button>}
      </div>
    </article>
  );
}

export function WorkDetail({ run, token, onApprove, onDeny }) {
  const [tab, setTab] = useState("Overview");
  const [progress, setProgress] = useState(null);
  const [events, setEvents] = useState([]);
  const [artifacts, setArtifacts] = useState([]);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [verifyMsg, setVerifyMsg] = useState("");

  useEffect(() => {
    if (!token || !run?.id) return;
    apiJson(`/api/v2/runs/${run.id}/progress`, { token }).then((r) => { if (r.ok) setProgress(r.data); }).catch(() => {});
    apiJson(`/api/v2/runs/${run.id}/events`, { token }).then((r) => { if (r.ok) setEvents(r.data); }).catch(() => {});
    apiJson(`/api/v2/runs/${run.id}/artifacts`, { token }).then((r) => { if (r.ok) setArtifacts(r.data); }).catch(() => {});
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

  async function verify(passed) {
    setVerifyBusy(true);
    setVerifyMsg("");
    try {
      const res = await apiJson(`/api/v2/runs/${run.id}/verify`, {
        method: "POST",
        token,
        body: { passed, checks: ["result-review"], summary: passed ? "Human verified the result." : "Human flagged an issue.", confidence: passed ? 0.9 : 0.2 },
      });
      if (res.ok) setVerifyMsg(`Verification recorded: ${res.data.event} (confidence ${res.data.result.confidence})`);
      else setVerifyMsg(res.data?.detail || "Verification failed");
    } catch (e) {
      setVerifyMsg(e.message || "Verification failed");
    } finally {
      setVerifyBusy(false);
    }
  }

  return (
    <section className="work-detail" aria-label={`Work detail ${run?.objective}`}>
      <header className="work-detail-head">
        <h3>{run?.objective}</h3>
        <WorkStatusBadge status={run?.status || "queued"} />
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
          <PlanList plan={progress?.plan || []} current={progress?.current_step} />
          {run?.status === "waiting_for_approval" && (
            <div className="approval-card rich">
              <h4>Needs your approval</h4>
              <p className="muted">Review scope, impact and reversibility before deciding.</p>
              <div className="row">
                <button className="btn btn-primary btn-sm" onClick={() => onApprove?.(run)}>Approve</button>
                <button className="btn btn-ghost btn-sm" onClick={() => onDeny?.(run)}>Deny</button>
              </div>
            </div>
          )}
          {failure && (
            <FailureState
              title="This step failed — here is what survived"
              detail={failure.payload?.error || failure.payload?.message}
              done={(progress?.plan || []).slice(0, 2)}
              missing={[progress?.current_step || "current step"].filter(Boolean)}
              actions={[{ label: "Retry step", variant: "primary" }, { label: "View trace" }]}
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
              <span className="muted small">{e.step_id || ""} · {e.payload?.agent || ""}</span>
            </li>
          ))}
          {events.length === 0 && <EmptyState title="No activity yet" hint="Events will stream here as agents work." />}
        </ol>
      )}

      {tab === "Agents" && (
        <div className="agent-grid">
          {agents.length === 0 && <EmptyState title="No agents yet" hint="Agents appear once they act on this work." />}
          {agents.map((a) => (
            <div key={a} className="agent-chip-card"><strong>@{a}</strong><span className="muted small"> participant</span></div>
          ))}
        </div>
      )}

      {tab === "Artifacts" && (
        <div className="artifact-grid">
          {artifacts.length === 0 && <EmptyState title="No artifacts" hint="Reports, diffs and files land here." />}
          {artifacts.map((a) => <ArtifactCard key={a.id} artifact={a} />)}
        </div>
      )}

      {tab === "Context" && (
        <div className="context-summary">
          <p className="muted">Evidence: {progress?.evidence_count || 0} · Decisions: {(progress?.decisions || []).length}</p>
          <ul>
            {(progress?.decisions || []).map((d, i) => <li key={i} className="small">{d.type}: {JSON.stringify(d.payload).slice(0, 160)}</li>)}
          </ul>
        </div>
      )}
    </section>
  );
}

function PlanList({ plan, current }) {
  if (!plan.length) return <p className="muted">Plan appears here once the run starts.</p>;
  return (
    <ol className="plan-list">
      {plan.map((step, i) => {
        const done = current ? plan.indexOf(current) > i : i < plan.length - 1;
        return <li key={i} className={done ? "done" : step === current ? "current" : "todo"}>{done ? "✓ " : step === current ? "● " : "○ "}{step}</li>;
      })}
    </ol>
  );
}
