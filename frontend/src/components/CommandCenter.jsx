import { useCallback, useEffect, useMemo, useState } from "react";
import { apiJson, Card, Button, Input, Textarea, Badge, EmptyState } from "../ui.jsx";
import ProviderPanel from "../ai-support/ProviderPanel.jsx";
import ModelPicker from "../ai-support/ModelPicker";
import { ChevronRight, Workflow, X, ChevronDown } from "lucide-react";
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
  const [routeHint, setRouteHint] = useState("");
  const [routeBusy, setRouteBusy] = useState(false);
  const [editing, setEditing] = useState(null);
  const [editNodes, setEditNodes] = useState([]);
  const [agentName, setAgentName] = useState("swarm");
  const [agentModel, setAgentModel] = useState("");
  const [providersCollapsed, setProvidersCollapsed] = useState(() => {
    try { return localStorage.getItem("swarm.providersCollapsed") === "1"; } catch { return false; }
  });

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
        setRouteHint(`${task_type} task${provider_name ? ` → ${provider_name}` : ""}${model ? ` · ${model}` : ""}. ${(reasons || [])[0] || ""}`);
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
          
          <h1>Make the work <em>legible.</em></h1>
          <p>Design a team, launch a supervised run, and keep the result after the agents are done.</p>
        </div>
        
      </div>
      <section className="cc-grid">
        <Card padded className="cc-launch-card">
          <div className="cc-section-head"><h2>Launch a run</h2><Badge variant={llmReady ? "success" : "warning"}>{llmReady ? "Model ready" : "No provider"}</Badge></div>
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
            {!llmReady && <p className="cc-provider-note">Connect an API key in AI providers below, or set GROQ_API_KEY in your environment.</p>}
            <Button variant="primary" type="submit" disabled={busy || !objective.trim() || !llmReady}>Launch run</Button>
          </form>
        </Card>
        <Card padded className="cc-workflow-card">
          <div className="cc-section-head"><h2>Workflows</h2><span className="cc-count">{workflows.length}</span></div>
          {workflows.length ? workflows.slice(0, 3).map(w => <button className="cc-list-row" key={w.id} onClick={() => openEditor(w)}><Workflow size={13} className="workflow-glyph" aria-hidden /><span><b>{w.name}</b><small>{w.description || "Supervised workflow"}</small></span><ChevronRight size={13} className="cc-row-arrow" aria-hidden /></button>) : <EmptyState kind="workflow" title="No workflows yet" message="Create your first reusable team below." />}
          <form onSubmit={createWorkflow} className="cc-inline-form"><Input value={name} onChange={e => setName(e.target.value)} placeholder="New workflow name" /><Button type="submit" variant="ghost" disabled={busy || !name.trim()}>Create</Button></form>
        </Card>
      </section>
      {editing && <div className="workflow-editor-backdrop"><section className="workflow-editor"><header className="run-monitor-head"><div><h2>{editing.name}</h2></div><button className="panel-close" onClick={() => setEditing(null)} aria-label="Close designer"><X size={14} /></button></header><div className="workflow-node-list">{editNodes.map((node, index) => <div className="workflow-node" key={node.id}><span className="node-index">{index + 1}</span><span><b>{node.label}</b><small>{node.type === "agent" ? `${node.agent}${node.model ? ` · ${node.model}` : ""}` : node.type}</small></span><button className="node-remove" onClick={() => setEditNodes(nodes => nodes.filter(n => n.id !== node.id))} aria-label={`Remove ${node.label}`}><X size={12} /></button></div>)}</div><div className="cc-inline-form workflow-add"><select className="input" value={agentName} onChange={e => setAgentName(e.target.value)}>{agents.length ? agents.map(a => <option key={a.name} value={a.name}>{a.display_name || a.name}</option>) : <option value="swarm">swarm</option>}</select><ModelPicker token={token} providerId={connectedProvider?.id} value={agentModel} onChange={setAgentModel} placeholder="Model from API" /><Button variant="ghost" onClick={() => setEditNodes(nodes => [...nodes, { id: `agent-${Date.now()}`, type: "agent", label: `Agent ${agentName}`, agent: agentName, ...(agentModel.trim() ? { model: agentModel.trim() } : {}) }])}>Add agent</Button></div><footer className="workflow-editor-actions"><Button variant="ghost" onClick={() => setEditing(null)}>Cancel</Button><Button variant="primary" disabled={busy} onClick={saveEditor}>Save workflow</Button></footer></section></div>}
      <section className="cc-runs"><div className="cc-section-head"><h2>Runs</h2><span className="cc-count">{runs.length}</span></div>{runs.length ? <div className="cc-run-list">{runs.slice(0, 8).map(r => <button className="cc-run-row" key={r.id} onClick={() => onOpenRun?.(r)}><Lamp tone={r.status === "completed" ? "go" : r.status === "failed" ? "hold" : "amber"} /><span className="run-objective">{r.objective}</span><Badge variant={r.status === "completed" ? "success" : r.status === "failed" ? "error" : "subtle"}>{r.status.replaceAll("_", " ")}</Badge><span className="text-subtle" style={{ fontSize: 12 }}>{new Date(r.created_at * 1000).toLocaleString()}</span></button>)}</div> : <EmptyState kind="run" title="No runs yet" message="Launch a brief and your live run history will appear here." />}</section>
      <section className={`cc-providers${providersCollapsed ? " collapsed" : ""}`}><div className="cc-section-head"><h2>AI providers</h2><span style={{ display: "flex", alignItems: "center", gap: 10 }}><span className="cc-provider-note" style={{ display: providersCollapsed ? "none" : undefined }}>API key or supported OAuth</span><button className="btn btn-ghost btn-sm" aria-expanded={!providersCollapsed} aria-label={providersCollapsed ? "Expand providers" : "Minimise providers"} onClick={() => { const next = !providersCollapsed; setProvidersCollapsed(next); try { localStorage.setItem("swarm.providersCollapsed", next ? "1" : "0"); } catch {} }}>{providersCollapsed ? "Show" : "Minimise"} <ChevronDown size={12} aria-hidden /></button></span></div>{!providersCollapsed && <ProviderPanel token={token} onStatusChange={load} flash={(message, error) => flash?.(message, error ? "error" : "success")} />}{providersCollapsed && <div className="cc-provider-collapsed-note"><span className="cc-provider-note">Collapsed — {providers.filter(p => p.connected).length} connected</span> <button className="btn btn-subtle btn-sm" onClick={() => { setProvidersCollapsed(false); try { localStorage.setItem("swarm.providersCollapsed", "0"); } catch {} }}>Expand</button></div>}</section>
    </div>
  );
}
