import { useEffect, useState } from "react";
import { apiJson, Button, Card, EmptyState, Input, Textarea } from "../ui.jsx";

export function KnowledgeView({ token, flash }) {
  const [docs, setDocs] = useState([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [tags, setTags] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    const res = await apiJson("/api/knowledge?limit=50", { token });
    if (res.ok) setDocs(res.data);
  }

  useEffect(() => { load(); }, [token]);

  async function search(e) {
    e?.preventDefault();
    if (query.trim().length < 2) { setResults(null); return; }
    setSearching(true);
    const res = await apiJson(`/api/knowledge/search?q=${encodeURIComponent(query.trim())}`, { token });
    setSearching(false);
    if (res.ok) setResults(res.data);
    else flash(res.data?.detail || "Search failed", "error");
  }

  async function submit(e) {
    e.preventDefault();
    if (!title.trim() || !body.trim()) return;
    setBusy(true);
    const payload = { title: title.trim(), body: body.trim(), tags: tags.trim() };
    const res = editingId
      ? await apiJson(`/api/knowledge/${editingId}`, { token, method: "PATCH", body: payload })
      : await apiJson("/api/knowledge", { token, method: "POST", body: payload });
    setBusy(false);
    if (res.ok) {
      flash(editingId ? "Knowledge updated" : "Knowledge saved", "success");
      setTitle(""); setBody(""); setTags(""); setEditingId(null);
      setResults(null); setQuery("");
      load();
    } else flash(res.data?.detail || "Save failed", "error");
  }

  function edit(doc) {
    setEditingId(doc.id);
    setTitle(doc.title || "");
    setBody(doc.body || "");
    setTags(doc.tags || "");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function remove(doc) {
    if (!window.confirm(`Delete knowledge "${doc.title}"? Agents will no longer see it.`)) return;
    const res = await apiJson(`/api/knowledge/${doc.id}`, { token, method: "DELETE" });
    if (res.ok) { flash("Knowledge deleted", "info"); setResults(null); load(); }
    else flash(res.data?.detail || "Delete failed", "error");
  }

  const shown = results || docs;

  return (
    <div id="log" className="knowledge-view">
      <div className="knowledge-head">
        <div>
          <h2 className="knowledge-title">What should agents remember?</h2>
          <p className="knowledge-sub">Saved docs are searchable by you here and by agents through knowledge search.</p>
        </div>
        <form className="knowledge-search" onSubmit={search} role="search">
          <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search knowledge…" aria-label="Search knowledge" />
          <Button variant="ghost" type="submit" disabled={searching}>{searching ? "…" : "Search"}</Button>
          {results && <Button variant="ghost" type="button" onClick={() => { setResults(null); setQuery(""); }}>Clear</Button>}
        </form>
      </div>

      <Card padded className="knowledge-form-card">
        <form onSubmit={submit} className="knowledge-form">
          <span className="reg">{editingId ? "Edit doc" : "New doc"}</span>
          <Input value={title} onChange={e => setTitle(e.target.value)} placeholder="Title — e.g. Deploy runbook" aria-label="Knowledge title" />
          <Textarea value={body} onChange={e => setBody(e.target.value)} placeholder="The durable fact, runbook, or convention…" rows={4} aria-label="Knowledge body" />
          <Input value={tags} onChange={e => setTags(e.target.value)} placeholder="tags (optional, comma separated)" aria-label="Knowledge tags" />
          <div className="knowledge-form-actions">
            {editingId && <Button variant="ghost" type="button" onClick={() => { setEditingId(null); setTitle(""); setBody(""); setTags(""); }}>Cancel</Button>}
            <Button variant="primary" type="submit" disabled={busy || !title.trim() || !body.trim()}>
              {busy ? "Saving…" : editingId ? "Update" : "Save"}
            </Button>
          </div>
        </form>
      </Card>

      <div className="knowledge-list">
        {shown.length === 0 && (
          <EmptyState
            kind="docs"
            title={results ? "No matches" : "No knowledge yet"}
            message={results ? "Try different keywords." : "Save runbooks, decisions, and conventions your agents should reuse."}
          />
        )}
        {shown.map(d => (
          <Card key={d.id} padded className="knowledge-card">
            <div className="knowledge-card-head">
              <span className="knowledge-card-title">{d.title}</span>
              {d.tags && <span className="tool-chip">{d.tags}</span>}
            </div>
            <p className="knowledge-card-body">{d.body}</p>
            <div className="knowledge-card-actions">
              <button className="msg-mini-action" onClick={() => edit(d)}>Edit</button>
              <button className="msg-mini-action danger" onClick={() => remove(d)}>Delete</button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
