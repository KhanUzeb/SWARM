import { useCallback, useEffect, useState } from "react";
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

  // Defect: `flash` was called bare in four places, so any surface that mounts
  // this view without a flasher threw instead of showing the failure.
  const say = (message, type) => flash?.(message, type);

  const load = useCallback(async (isLive = () => true) => {
    try {
      const res = await apiJson("/api/knowledge?limit=50", { token });
      // Defect: a null body landed in `docs` and the next `shown.length`
      // render threw on it.
      if (isLive() && res.ok) setDocs(res.data || []);
    } catch {
      if (isLive()) setDocs([]);
    }
  }, [token]);

  useEffect(() => {
    let alive = true;
    // Defect: the initial load was never cancelled, so navigating away
    // mid-flight set state on an unmounted KnowledgeView.
    void load(() => alive);
    return () => { alive = false; };
  }, [load]);

  async function search(e) {
    e?.preventDefault();
    if (query.trim().length < 2) { setResults(null); return; }
    setSearching(true);
    try {
      const res = await apiJson(`/api/knowledge/search?q=${encodeURIComponent(query.trim())}`, { token });
      if (res.ok) setResults(res.data || []);
      else say(res.data?.detail || "Search failed", "error");
    } catch (err) {
      // Defect: a rejected request left `searching` true forever, so the
      // Search button never came back.
      say(err.message || "Search failed", "error");
    } finally {
      setSearching(false);
    }
  }

  async function submit(e) {
    e.preventDefault();
    if (!title.trim() || !body.trim()) return;
    setBusy(true);
    const payload = { title: title.trim(), body: body.trim(), tags: tags.trim() };
    try {
      const res = editingId
        ? await apiJson(`/api/knowledge/${editingId}`, { token, method: "PATCH", body: payload })
        : await apiJson("/api/knowledge", { token, method: "POST", body: payload });
      if (res.ok) {
        say(editingId ? "Knowledge updated" : "Knowledge saved", "success");
        setTitle(""); setBody(""); setTags(""); setEditingId(null);
        setResults(null); setQuery("");
        load();
      } else say(res.data?.detail || "Save failed", "error");
    } catch (err) {
      // Defect: a rejected save left the Save button spinning for good.
      say(err.message || "Save failed", "error");
    } finally {
      setBusy(false);
    }
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
    try {
      const res = await apiJson(`/api/knowledge/${doc.id}`, { token, method: "DELETE" });
      if (res.ok) { say("Knowledge deleted", "info"); setResults(null); load(); }
      else say(res.data?.detail || "Delete failed", "error");
    } catch (err) {
      say(err.message || "Delete failed", "error");
    }
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
            kind="search"
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
              {/* Not .msg-mini-action: that is a 22x22 icon square that also
                  stays at opacity 0 until the row is hovered, so the words
                  "Edit" and "Delete" overflowed the box and were invisible
                  until hover. Real buttons, real size. */}
              <button className="btn btn-ghost btn-sm" onClick={() => edit(d)}>Edit</button>
              <button className="btn btn-danger btn-sm" onClick={() => remove(d)}>Delete</button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
