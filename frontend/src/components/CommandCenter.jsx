import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiJson, Button, Input, Textarea, Badge, EmptyState, fmtTime } from "../ui.jsx";
import { isUnauthorized } from "../lib.js";
import ModelPicker from "../ai-support/ModelPicker";
import { ChevronRight, Workflow, X } from "lucide-react";
import { Lamp } from "./Flap";

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

export function CommandCenter({ token, flash, onOpenRun, onOpenConnect, onUnauthorized, agents = [] }) {
  const [workflows, setWorkflows] = useState([]);
  const [runs, setRuns] = useState([]);
  const [providers, setProviders] = useState([]);
  const [modelProviderId, setModelProviderId] = useState(null);
  // Readiness starts unknown, not ready: the badge used to claim "Model ready"
  // before the first answer had landed, and kept claiming it when the status
  // call never came back.
  const [llmReady, setLlmReady] = useState(false);
  const [statusKnown, setStatusKnown] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [runModel, setRunModel] = useState("");
  const [workflowId, setWorkflowId] = useState("");
  const [busy, setBusy] = useState(false);
  const [routeHint, setRouteHint] = useState("");
  const [routeBusy, setRouteBusy] = useState(false);
  const [editing, setEditing] = useState(null);
  const [editNodes, setEditNodes] = useState([]);
  const [agentName, setAgentName] = useState("swarm");
  const [agentModel, setAgentModel] = useState("");

  // Provider connections live in Connect now: this card keeps only the
  // readiness signal (the same providers fetch the launch form already
  // needs) and a link, so the provider panel mounts exactly once per session.
  const connectedCount = providers.filter(p => p.connected).length;

  const connectedProvider = useMemo(
    () => providers.find(p => p.id === modelProviderId) || firstConnectedProvider(providers),
    [providers, modelProviderId],
  );

  const load = useCallback(async () => {
    if (!token) return;
    // Promise.all dropped the first rejection on the floor, taking every panel
    // with it: one failed call left the whole board empty with nothing said.
    // allSettled keeps whatever answered and names what did not.
    const [w, r, p, status, models] = await Promise.allSettled([
      apiJson("/api/v2/workflows", { token }),
      apiJson("/api/v2/runs", { token }),
      apiJson("/api/v2/providers", { token }),
      apiJson("/api/status", { token, cacheTtl: 10_000 }),
      apiJson("/api/v2/models/connected", { token, cacheTtl: 0 }),
    ]);
    const answered = [w, r, p, status, models].filter((x) => x.status === "fulfilled");
    // A rejected token is not "an empty board": hand it back to the session.
    if (answered.some((x) => isUnauthorized(x.value))) { onUnauthorized?.(); return; }
    const value = (x) => (x.status === "fulfilled" ? x.value : null);
    if (value(w)?.ok) setWorkflows(value(w).data);
    if (value(r)?.ok) setRuns(value(r).data);
    if (value(p)?.ok) setProviders(value(p).data);
    const st = value(status);
    if (st) {
      setStatusKnown(true);
      setLlmReady(st.ok ? !!st.data?.llm_ready : false);
    }
    const m = value(models);
    if (m?.ok && m.data) {
      if (m.data.provider_id) setModelProviderId(m.data.provider_id);
      if (m.data.default_model) setRunModel((prev) => prev || m.data.default_model);
    }
    const failed = [w, r, p, status, models].find((x) => x.status === "rejected");
    setLoadError(failed ? "The board could not be loaded from the server." : "");
  }, [token, onUnauthorized]);

  useEffect(() => { load(); }, [load]);

  /** Every write ends its busy state, even when the request never comes back.
   *  setBusy(false) used to sit after the await, so one offline submit left the
   *  button spinning on "Creating…" for good. */
  async function write(run, failure) {
    setBusy(true);
    try {
      await run();
    } catch {
      flash?.(`${failure} — check your connection`, "error");
    } finally {
      setBusy(false);
    }
  }

  function createWorkflow(ev) {
    ev.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    return write(async () => {
      const res = await apiJson("/api/v2/workflows", {
        token, method: "POST",
        body: { name: trimmed, description: "Supervised agent workflow", graph: starterGraph },
      });
      if (res.ok) { setName(""); flash?.("Workflow created", "success"); load(); }
      else flash?.(res.data?.detail || "Could not create workflow", "error");
    }, "Could not create the workflow");
  }

  function launchRun(ev) {
    ev.preventDefault();
    const trimmed = objective.trim();
    if (!trimmed) return;
    if (!llmReady) {
      flash?.("Connect an AI provider in Connect before launching a run", "error");
      return;
    }
    return write(async () => {
      const body = { objective: trimmed, workflow_id: workflowId || null, policy: "supervised" };
      if (runModel.trim()) body.model = runModel.trim();
      const res = await apiJson("/api/v2/runs", { token, method: "POST", body });
      if (res.ok) { setObjective(""); flash?.("Run queued", "success"); load(); onOpenRun?.(res.data); }
      else flash?.(res.data?.detail || "Could not launch run", "error");
    }, "Could not launch the run");
  }

  async function suggestModel() {
    if (!objective.trim() || routeBusy) return;
    setRouteBusy(true);
    setRouteHint("");
    try {
      const res = await apiJson("/api/v2/model-routing/route", {
        token, method: "POST", body: { objective: objective.trim() },
      });
      if (res.ok && res.data) {
        const { task_type, provider_name, model, reasons } = res.data;
        if (model) {
          if (res.data.provider_id) setModelProviderId(res.data.provider_id);
          setRunModel(model);
        }
        setRouteHint(`${task_type} task${provider_name ? ` → ${provider_name}` : ""}${model ? `, ${model}` : ""}. ${(reasons || [])[0] || ""}`);
      } else {
        setRouteHint(res.data?.detail || "Could not suggest a model");
      }
    } catch (e) {
      setRouteHint(e.message || "Could not suggest a model");
    } finally {
      setRouteBusy(false);
    }
  }

  function openEditor(workflow) {
    setEditing(workflow);
    setEditNodes(workflow.graph?.nodes || []);
  }

  function saveEditor() {
    if (!editing) return;
    const graph = { ...(editing.graph || {}), nodes: editNodes, edges: editNodes.slice(0, -1).map((n, i) => [n.id, editNodes[i + 1].id]) };
    return write(async () => {
      const res = await apiJson(`/api/v2/workflows/${editing.id}`, { token, method: "PATCH", body: { graph } });
      if (res.ok) { setEditing(null); flash?.("Workflow saved", "success"); load(); }
      else flash?.(res.data?.detail || "Could not save workflow", "error");
    }, "Could not save the workflow");
  }

  return (
    <div id="command-center" className="command-center">
      <div className="cc-hero">
        <div>
          
          <h1>Make the work <em>legible.</em></h1>
          <p>Design a team, launch a supervised run, and keep the result after the agents are done.</p>
        </div>
        
      </div>
      <section className="cc-grid">
        {/* Not <Card padded className="cc-launch-card">: that is a card inside
            a card, two identical borders on the same edge (8a forbids nested
            cards). .cc-launch-card already declares the whole card. */}
        <section className="cc-launch-card">
          <div className="cc-section-head"><h2>Launch a run</h2><Badge variant={llmReady ? "success" : "warning"}>{!statusKnown ? "Checking" : llmReady ? "Model ready" : "No provider"}</Badge></div>
          <form onSubmit={launchRun} className="cc-form">
            <Textarea value={objective} onChange={e => setObjective(e.target.value)} placeholder="What should your agent team accomplish?" rows={4} />
            <label className="field-label" htmlFor="run-model">Model</label>
            <div className="row">
              <div style={{ flex: 1 }}>
                <ModelPicker
                  id="run-model"
                  token={token}
                  providerId={connectedProvider?.id}
                  value={runModel}
                  onChange={setRunModel}
                  autoSelectFirst
                  placeholder="Select a model from API"
                />
              </div>
              <Button variant="ghost" size="sm" onClick={suggestModel} disabled={!objective.trim() || routeBusy || !llmReady} title="Classify this brief and pick a connected model">
                {routeBusy ? "Routing…" : "Auto-route"}
              </Button>
            </div>
            {routeHint && <p className="muted small" role="status">{routeHint}</p>}
            {workflows.length > 0 && (
              <>
                <label className="field-label" htmlFor="run-workflow">Workflow (optional)</label>
                <select id="run-workflow" className="input" value={workflowId} onChange={e => setWorkflowId(e.target.value)}>
                  <option value="">Direct run — lead agent only</option>
                  {workflows.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                </select>
              </>
            )}
            {!llmReady && <p className="cc-provider-note">{statusKnown ? "No AI provider is connected — runs stay queued until one is." : "Checking the server for a connected AI provider…"}</p>}
            <Button variant="primary" type="submit" disabled={busy || !objective.trim() || !llmReady || !statusKnown}>Launch run</Button>
          </form>
        </section>
        {/* Same reasoning as the launch card: one card, not two. */}
        <section className="cc-workflow-card">
          <div className="cc-section-head"><h2>Workflows</h2><span className="cc-count">{workflows.length}</span></div>
          {workflows.length ? workflows.slice(0, 3).map(w => <button className="cc-list-row" key={w.id} onClick={() => openEditor(w)}><Workflow size={13} className="workflow-glyph" aria-hidden /><span><b>{w.name}</b><small>{w.description || "Supervised workflow"}</small></span><ChevronRight size={13} className="cc-row-arrow" aria-hidden /></button>) : <EmptyState kind="workflow" title="No workflows yet" message="Create your first reusable team below." />}
          <form onSubmit={createWorkflow} className="cc-inline-form"><Input value={name} onChange={e => setName(e.target.value)} placeholder="New workflow name" /><Button type="submit" variant="ghost" disabled={busy || !name.trim()}>Create</Button></form>
        </section>
      </section>
      {editing && (
        <div
          className="workflow-editor-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label={`Design ${editing.name}`}
          onClick={e => { if (e.target === e.currentTarget) setEditing(null); }}
        >
          <WorkflowEditor
            workflow={editing}
            nodes={editNodes}
            setNodes={setEditNodes}
            agents={agents}
            token={token}
            providerId={connectedProvider?.id}
            model={agentModel}
            setModel={setAgentModel}
            agentName={agentName}
            setAgentName={setAgentName}
            busy={busy}
            onSave={saveEditor}
            onClose={() => setEditing(null)}
          />
        </div>
      )}
      <section className="cc-runs"><div className="cc-section-head"><h2>Runs</h2><span className="cc-count">{runs.length}</span></div>{loadError && <p className="cc-provider-note" role="alert">{loadError} <Button variant="ghost" size="sm" onClick={load}>Retry</Button></p>}{runs.length ? <div className="cc-run-list">{runs.slice(0, 8).map(r => <button className="cc-run-row" key={r.id} onClick={() => onOpenRun?.(r)}><Lamp tone={r.status === "failed" ? "hold" : r.status === "cancelled" ? "off" : "go"} /><span className="run-objective">{r.objective}</span><Badge variant={r.status === "completed" ? "success" : r.status === "failed" ? "error" : "subtle"}>{r.status.replaceAll("_", " ")}</Badge>{/* fmtTime, not new Date(seconds * 1000): the run endpoint answers with
              an ISO string on some paths, which that turned into "Invalid Date" */}
            <span className="mono muted">{fmtTime(r.created_at)}</span></button>)}</div> : <EmptyState kind="run" title="No runs yet" message="Launch a brief and your live run history will appear here." />}</section>
      <section className="cc-providers" aria-label="AI provider status">
        <div className="cc-section-head">
          <h2>AI providers</h2>
        </div>
        <div className="row">
          <Lamp tone={llmReady ? "go" : "hold"} />
          <p className="cc-provider-note" role="status" style={{ margin: 0, flex: 1 }}>
            {connectedProvider
              ? `${connectedCount} connected — answering via ${connectedProvider.name}.`
              : "No AI provider connected — runs stay queued until one is."}
          </p>
          <Button variant="secondary" size="sm" onClick={onOpenConnect}>Open Connect</Button>
        </div>
        {!llmReady && statusKnown && <p className="cc-provider-note">Connect an API key in Connect, or set GROQ_API_KEY in your environment.</p>}
      </section>
    </div>
  );
}

/**
 * The workflow designer. Its own component so the Escape listener, the dialog
 * semantics and the node list read as one thing, and so CommandCenter's render
 * stays a page rather than a modal with an escape hatch bolted on.
 *
 * No sequential index beside the node label — Section 8a forbids section
 * numbers, and the node's own order in the list is the order it runs in.
 */
function WorkflowEditor({ workflow, nodes, setNodes, agents, token, providerId, model, setModel, agentName, setAgentName, busy, onSave, onClose }) {
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose?.(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <section className="workflow-editor" aria-label={`Design ${workflow.name}`}>
      <header className="run-monitor-head">
        <div><h2>{workflow.name}</h2></div>
        <button className="panel-close" onClick={onClose} aria-label="Close designer"><X size={14} /></button>
      </header>
      <div className="workflow-node-list">
        {nodes.map(node => (
          <div className="workflow-node" key={node.id}>
            <span><b>{node.label}</b><small>{node.type === "agent" ? `${node.agent}${node.model ? `, ${node.model}` : ""}` : node.type}</small></span>
            <button className="node-remove" onClick={() => setNodes(list => list.filter(n => n.id !== node.id))} aria-label={`Remove ${node.label}`}><X size={12} /></button>
          </div>
        ))}
      </div>
      <div className="cc-inline-form workflow-add">
        <select className="input" value={agentName} onChange={e => setAgentName(e.target.value)} aria-label="Agent to add">
          {agents.length ? agents.map(a => <option key={a.name} value={a.name}>{a.display_name || a.name}</option>) : <option value="swarm">swarm</option>}
        </select>
        <ModelPicker token={token} providerId={providerId} value={model} onChange={setModel} placeholder="Model from API" />
        <Button variant="ghost" onClick={() => setNodes(list => [...list, { id: `agent-${Date.now()}`, type: "agent", label: `Agent ${agentName}`, agent: agentName, ...(model.trim() ? { model: model.trim() } : {}) }])}>Add agent</Button>
      </div>
      <footer className="workflow-editor-actions">
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={busy} onClick={onSave}>Save workflow</Button>
      </footer>
    </section>
  );
}
