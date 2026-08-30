import { useCallback, useEffect, useMemo, useState } from "react";
import { apiJson, Card, Button, Input, Textarea, Badge, EmptyState } from "../ui.jsx";
import ProviderPanel from "../ai-support/ProviderPanel.jsx";
import ModelPicker from "../ai-support/ModelPicker.jsx";

const starterGraph = {
  nodes: [
    { id: "brief", type: "input", label: "Brief", description: "The objective and context" },
    { id: "lead", type: "agent", label: "Lead agent", agent: "swarm" },
    { id: "report", type: "output", label: "Run report" },
  ],
  edges: [["brief", "lead"], ["lead", "report"]],
};

function firstConnectedProvider(providers) {
  return providers.find(p => p.connected && p.via === "stored") || providers.find(p => p.connected) || null;
}

export function CommandCenter({ token, flash, onOpenRun, agents = [] }) {
  const [workflows, setWorkflows] = useState([]);
  const [runs, setRuns] = useState([]);
  const [providers, setProviders] = useState([]);
  const [modelProviderId, setModelProviderId] = useState(null);
  const [llmReady, setLlmReady] = useState(true);
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [runModel, setRunModel] = useState("");
  const [workflowId, setWorkflowId] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(null);
  const [editNodes, setEditNodes] = useState([]);
  const [agentName, setAgentName] = useState("swarm");
  const [agentModel, setAgentModel] = useState("");

  const connectedProvider = useMemo(
    () => providers.find(p => p.id === modelProviderId) || firstConnectedProvider(providers),
    [providers, modelProviderId],
  );

  const load = useCallback(async () => {
    if (!token) return;
    const [w, r, p, status, models] = await Promise.all([
      apiJson("/api/v2/workflows", { token }),
      apiJson("/api/v2/runs", { token }),
      apiJson("/api/v2/providers", { token }),
      apiJson("/api/status", { token, cacheTtl: 10_000 }),
      apiJson("/api/v2/models/connected", { token, cacheTtl: 0 }),
    ]);
    if (w.ok) setWorkflows(w.data);
    if (r.ok) setRuns(r.data);
    if (p.ok) setProviders(p.data);
    if (status.ok) setLlmReady(!!status.data?.llm_ready);
    if (models.ok && models.data) {
      if (models.data.provider_id) setModelProviderId(models.data.provider_id);
      if (models.data.default_model) setRunModel(prev => prev || models.data.default_model);
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  async function createWorkflow(ev) {
    ev.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    const res = await apiJson("/api/v2/workflows", { token, method: "POST", body: { name: name.trim(), description: "Supervised agent workflow", graph: starterGraph } });
    setBusy(false);
    if (res.ok) { setName(""); flash?.("Workflow created", "success"); load(); }
    else flash?.(res.data?.detail || "Could not create workflow", "error");
  }

  async function launchRun(ev) {
    ev.preventDefault();
    if (!objective.trim()) return;
    if (!llmReady) {
      flash?.("Connect an AI provider below before launching a run", "error");
      return;
    }
    setBusy(true);
    const body = {
      objective: objective.trim(),
      workflow_id: workflowId || null,
      policy: "supervised",
    };
    if (runModel.trim()) body.model = runModel.trim();
    const res = await apiJson("/api/v2/runs", { token, method: "POST", body });
    setBusy(false);
    if (res.ok) { setObjective(""); flash?.("Run queued", "success"); load(); onOpenRun?.(res.data); }
    else flash?.(res.data?.detail || "Could not launch run", "error");
  }

  function openEditor(workflow) {
    setEditing(workflow);
    setEditNodes(workflow.graph?.nodes || []);
  }

  async function saveEditor() {
    if (!editing) return;
    setBusy(true);
    const graph = { ...(editing.graph || {}), nodes: editNodes, edges: editNodes.slice(0, -1).map((n, i) => [n.id, editNodes[i + 1].id]) };
    const res = await apiJson(`/api/v2/workflows/${editing.id}`, { token, method: "PATCH", body: { graph } });
    setBusy(false);
    if (res.ok) { setEditing(null); flash?.("Workflow saved", "success"); load(); }
    else flash?.(res.data?.detail || "Could not save workflow", "error");
  }

  return (
    <div id="command-center" className="command-center">
      <div className="cc-hero">
        <div>
          <span className="eyebrow">Overview</span>
          <h1>Make the work legible.</h1>
          <p>Design a team, launch a supervised run, and keep the result after the agents are done.</p>
        </div>
        <div className="cc-hero-mark" aria-hidden>✦</div>
      </div>
      <section className="cc-grid">
        <Card padded className="cc-launch-card">
          <div className="cc-section-head"><div><span className="eyebrow">Start work</span><h2>Launch a run</h2></div><Badge variant={llmReady ? "success" : "warning"}>{llmReady ? "Model ready" : "No provider"}</Badge></div>
          <form onSubmit={launchRun} className="cc-form">
            <Textarea value={objective} onChange={e => setObjective(e.target.value)} placeholder="What should your agent team accomplish?" rows={4} />
            <label className="field-label" htmlFor="run-model">Model</label>
            <ModelPicker
              id="run-model"
              token={token}
              providerId={connectedProvider?.id}
              value={runModel}
              onChange={setRunModel}
              autoSelectFirst
              placeholder={connectedProvider ? "Select a model from API" : "Connect a provider first"}
              disabled={!connectedProvider}
            />
            {workflows.length > 0 && (
              <>
                <label className="field-label" htmlFor="run-workflow">Workflow (optional)</label>
                <select id="run-workflow" className="input" value={workflowId} onChange={e => setWorkflowId(e.target.value)}>
                  <option value="">Direct run — lead agent only</option>
                  {workflows.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                </select>
              </>
            )}
            {!llmReady && <p className="cc-provider-note">Connect an API key in AI providers below, or set GROQ_API_KEY in your environment.</p>}
            <Button variant="primary" type="submit" disabled={busy || !objective.trim() || !llmReady}>Launch run</Button>
          </form>
        </Card>
        <Card padded className="cc-workflow-card">
          <div className="cc-section-head"><div><span className="eyebrow">Team design</span><h2>Workflows</h2></div><span className="cc-count">{workflows.length}</span></div>
          {workflows.length ? workflows.slice(0, 3).map(w => <button className="cc-list-row" key={w.id} onClick={() => openEditor(w)}><span className="workflow-glyph">⌘</span><span><b>{w.name}</b><small>{w.description || "Supervised workflow"}</small></span><span>→</span></button>) : <EmptyState icon="◇" title="No workflows yet" message="Create your first reusable team below." />}
          <form onSubmit={createWorkflow} className="cc-inline-form"><Input value={name} onChange={e => setName(e.target.value)} placeholder="New workflow name" /><Button type="submit" variant="ghost" disabled={busy || !name.trim()}>Create</Button></form>
        </Card>
      </section>
      {editing && <div className="workflow-editor-backdrop"><section className="workflow-editor"><header className="run-monitor-head"><div><span className="eyebrow">Workflow designer</span><h2>{editing.name}</h2></div><button className="panel-close" onClick={() => setEditing(null)}>×</button></header><div className="workflow-node-list">{editNodes.map((node, index) => <div className="workflow-node" key={node.id}><span className="node-index">{index + 1}</span><span><b>{node.label}</b><small>{node.type === "agent" ? `${node.agent}${node.model ? ` · ${node.model}` : ""}` : node.type}</small></span><button className="node-remove" onClick={() => setEditNodes(nodes => nodes.filter(n => n.id !== node.id))}>×</button></div>)}</div><div className="cc-inline-form workflow-add"><select className="input" value={agentName} onChange={e => setAgentName(e.target.value)}>{agents.length ? agents.map(a => <option key={a.name} value={a.name}>{a.display_name || a.name}</option>) : <option value="swarm">swarm</option>}</select><ModelPicker token={token} providerId={connectedProvider?.id} value={agentModel} onChange={setAgentModel} placeholder="Model from API" disabled={!connectedProvider} /><Button variant="ghost" onClick={() => setEditNodes(nodes => [...nodes, { id: `agent-${Date.now()}`, type: "agent", label: `Agent ${agentName}`, agent: agentName, ...(agentModel.trim() ? { model: agentModel.trim() } : {}) }])}>Add agent</Button></div><footer className="workflow-editor-actions"><Button variant="ghost" onClick={() => setEditing(null)}>Cancel</Button><Button variant="primary" disabled={busy} onClick={saveEditor}>Save workflow</Button></footer></section></div>}
      <section className="cc-runs"><div className="cc-section-head"><div><span className="eyebrow">Recent activity</span><h2>Runs</h2></div><span className="cc-count">{runs.length}</span></div>{runs.length ? <div className="cc-run-list">{runs.slice(0, 8).map(r => <button className="cc-run-row" key={r.id} onClick={() => onOpenRun?.(r)}><span className={`run-status ${r.status}`} /><span className="run-objective">{r.objective}</span><Badge variant={r.status === "completed" ? "success" : r.status === "failed" ? "error" : "subtle"}>{r.status.replaceAll("_", " ")}</Badge><span className="text-subtle" style={{ fontSize: 12 }}>{new Date(r.created_at * 1000).toLocaleString()}</span></button>)}</div> : <EmptyState icon="◌" title="No runs yet" message="Launch a brief and your live run history will appear here." />}</section>
      <section className="cc-providers"><div className="cc-section-head"><div><span className="eyebrow">Model access</span><h2>AI providers</h2></div><span className="cc-provider-note">API key or supported OAuth</span></div><ProviderPanel token={token} onStatusChange={load} flash={(message, error) => flash?.(message, error ? "error" : "success")} /></section>
    </div>
  );
}

function eventDetail(event) {
  const payload = event.payload || {};
  if (payload.output) return payload.output;
  if (payload.error) return payload.error;
  if (payload.objective) return payload.objective;
  if (payload.decision) return `Decision: ${payload.decision}`;
  return "";
}

export function RunMonitor({ token, run, onClose }) {
  const [current, setCurrent] = useState(run);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");
  const [approvalBusy, setApprovalBusy] = useState(false);

  const refresh = useCallback(async () => {
    if (!run?.id) return;
    const [r, e] = await Promise.all([
      apiJson(`/api/v2/runs/${run.id}`, { token }),
      apiJson(`/api/v2/runs/${run.id}/events?after=0`, { token }),
    ]);
    if (r.ok) setCurrent(r.data); else setError("Run is no longer available");
    if (e.ok) setEvents(e.data);
  }, [token, run?.id]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 2500);
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${proto}://${location.host}/api/v2/ws/runs/${encodeURIComponent(run.id)}`);
    socket.onopen = () => socket.send(JSON.stringify({ token, after: 0 }));
    socket.onmessage = message => {
      try {
        const event = JSON.parse(message.data);
        if (event.type === "run_terminal") { setCurrent(value => ({ ...value, status: event.status })); return; }
        if (event.seq) setEvents(values => [...values.filter(value => value.seq !== event.seq), event].sort((a, b) => a.seq - b.seq));
      } catch { /* polling remains the fallback */ }
    };
    return () => { clearInterval(timer); socket.close(); };
  }, [refresh, run.id, token]);

  async function resolveApproval(event, decision) {
    if (!event.step_id) return;
    setApprovalBusy(true);
    const response = await apiJson(`/api/v2/runs/${run.id}/approvals/${event.step_id}`, { token, method: "POST", body: { status: decision } });
    setApprovalBusy(false);
    if (response.ok) refresh(); else setError(response.data?.detail || "Could not resolve approval");
  }

  async function downloadArtifact(event, artifact) {
    event.preventDefault();
    const response = await fetch(`/api/v2/artifacts/${artifact.id}`, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) return;
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = artifact.name || "artifact"; link.click();
    URL.revokeObjectURL(url);
  }

  if (!run) return null;
  return (
    <div className="run-monitor-backdrop" role="dialog" aria-modal="true">
      <section className="run-monitor">
        <header className="run-monitor-head"><div><span className="eyebrow">Live run</span><h2>{current?.objective || run.objective}</h2><span className="text-subtle" style={{ fontSize: 12 }}>{current?.id || run.id}{current?.model ? ` · ${current.model}` : ""}</span></div><button className="panel-close" onClick={onClose} aria-label="Close">×</button></header>
        <div className="run-monitor-status"><span className={`run-status ${current?.status}`} /><strong>{(current?.status || "queued").replaceAll("_", " ")}</strong><span className="text-subtle">Supervised execution</span></div>
        {error && <p className="run-monitor-error">{error}</p>}
        <div className="run-event-list">{events.length ? events.map(e => {
          const detail = eventDetail(e);
          const isError = detail.includes("[agent error:");
          return (
            <div className={`run-event${isError ? " run-event-error" : ""}`} key={`${e.run_id}-${e.seq}`}>
              <span className="event-seq">{String(e.seq).padStart(2, "0")}</span>
              <span>
                <b>{e.event_type.replaceAll("_", " ")}</b>
                <small>{e.step_id || "run"} · {new Date(e.created_at * 1000).toLocaleTimeString()}</small>
                {detail && <p className="run-event-detail">{detail}</p>}
                {e.event_type === "approval_requested" && current?.status === "waiting_for_approval" && (
                  <span className="run-approval-actions">
                    <Button size="sm" variant="primary" disabled={approvalBusy} onClick={() => resolveApproval(e, "approved")}>Approve</Button>
                    <Button size="sm" variant="ghost" disabled={approvalBusy} onClick={() => resolveApproval(e, "denied")}>Deny</Button>
                  </span>
                )}
              </span>
            </div>
          );
        }) : <EmptyState icon="◌" title="Waiting for events" message="The run is queued and will appear here as execution begins." />}</div>
        {current?.report && <article className="run-report"><span className="eyebrow">Run report</span><h3>{current.report.summary || "Completed run"}</h3><p>{current.report.steps?.length || 0} workflow steps completed.</p>{current.report.artifacts?.map(a => <a className="run-artifact" key={a.id} href={`/api/v2/artifacts/${a.id}`} onClick={event => downloadArtifact(event, a)}>Download {a.name}</a>)}</article>}
      </section>
    </div>
  );
}
