import * as React from "react";
import { Search, CornerDownLeft } from "lucide-react";

interface CommandItem {
  id: string;
  label: string;
  hint?: string;
  shortcut?: string;
  keywords?: string[];
  group?: string;
}

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  commands: CommandItem[];
  onCommand: (cmd: CommandItem) => void;
}

export function CommandPalette({ open, onClose, commands = [], onCommand }: CommandPaletteProps) {
  const [query, setQuery] = React.useState("");
  const [index, setIndex] = React.useState(0);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const listRef = React.useRef<HTMLDivElement>(null);

  const filtered = React.useMemo(() => {
    if (!query.trim()) return commands;
    const q = query.toLowerCase();
    return commands.filter(
      (c) =>
        c.label.toLowerCase().includes(q) ||
        c.keywords?.some((k) => k.toLowerCase().includes(q)) ||
        c.group?.toLowerCase().includes(q),
    );
  }, [query, commands]);

  const groups = React.useMemo(() => {
    const map: Record<string, CommandItem[]> = {};
    for (const c of filtered) {
      const g = c.group || "Commands";
      if (!map[g]) map[g] = [];
      map[g].push(c);
    }
    return map;
  }, [filtered]);

  React.useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
      setTimeout(() => inputRef.current?.focus(), 15);
    }
  }, [open]);

  React.useEffect(() => {
    setIndex(0);
  }, [query]);

  React.useEffect(() => {
    if (listRef.current && filtered[index]) {
      const el = listRef.current.querySelector(`[data-index="${index}"]`) as HTMLElement | null;
      el?.scrollIntoView({ block: "nearest" });
    }
  }, [index, filtered]);

  if (!open) return null;

  let flatIndex = -1;

  function handleKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      onClose();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setIndex((i) => Math.min(i + 1, filtered.length - 1));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setIndex((i) => Math.max(i - 1, 0));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const cmd = filtered[index];
      if (cmd) {
        onCommand(cmd);
        onClose();
      }
    }
  }

  return (
    <div className="cmd-palette" role="dialog" aria-modal="true" aria-label="Command palette" onClick={onClose}>
      <div className="cmd-panel" onClick={(e) => e.stopPropagation()}>
        <div className="cmd-input-row">
          <Search size={15} className="shrink-0" style={{ color: "var(--ink-3)" }} />
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Name a room, a teammate, or a command"
            className="input"
            style={{ border: 0, boxShadow: "none", background: "transparent", padding: 0 }}
            aria-label="Search commands"
          />
          <span className="reg">esc</span>
        </div>

        <div ref={listRef} className="cmd-list scroll-y">
          {filtered.length === 0 && (
            <div className="cmd-empty">
              Nothing on the board matches “{query.trim()}”.
            </div>
          )}

          {Object.entries(groups).map(([group, items]) => (
            <div key={group}>
              <div className="dropdown-section reg">{group}</div>
              {items.map((item) => {
                flatIndex++;
                const fi = flatIndex;
                const isActive = fi === index;
                return (
                  <button
                    key={item.id}
                    data-index={fi}
                    type="button"
                    onMouseEnter={() => setIndex(fi)}
                    onClick={() => {
                      onCommand(item);
                      onClose();
                    }}
                    className="cmd-item"
                    data-active={isActive}
                  >
                    <span className="label">{item.label}</span>
                    {item.hint && <span className="reg">{item.hint}</span>}
                    {item.shortcut && <span className="reg reg-lit">{item.shortcut}</span>}
                    {isActive && <CornerDownLeft size={12} className="shrink-0" />}
                  </button>
                );
              })}
            </div>
          ))}
        </div>

        <div className="composer-hints" style={{ padding: "8px 16px", borderTop: "1px solid var(--rule)" }}>
          <span>Arrow keys move</span>
          <span>Enter opens</span>
          <span style={{ marginLeft: "auto" }}>Esc closes</span>
        </div>
      </div>
    </div>
  );
}
