import * as React from "react";
import { Search, CornerDownLeft, Sparkles } from "lucide-react";

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

export function CommandPalette({
  open,
  onClose,
  commands = [],
  onCommand,
}: CommandPaletteProps) {
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
        c.group?.toLowerCase().includes(q)
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
      const el = listRef.current.querySelector(
        `[data-index="${index}"]`
      ) as HTMLElement | null;
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
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-16 sm:pt-24 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <div
        className="fixed inset-0 bg-black/70 backdrop-blur-sm animate-in fade-in-0 duration-150"
        aria-hidden="true"
      />
      <div
        className="relative z-50 w-full max-w-xl overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-900/95 text-zinc-100 shadow-2xl backdrop-blur-xl animate-in zoom-in-95 fade-in-0 duration-150 flex flex-col max-h-[75vh]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search Input Bar */}
        <div className="flex items-center gap-3 px-4 py-3.5 border-b border-zinc-800/80 bg-zinc-950/40">
          <Search className="w-4 h-4 text-zinc-400 shrink-0" />
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Type a command or search…"
            className="flex-1 bg-transparent text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none"
            aria-label="Search commands"
          />
          <kbd className="hidden sm:inline-block px-1.5 py-0.5 text-[10px] font-mono text-zinc-500 bg-zinc-850 rounded border border-zinc-750">
            ESC
          </kbd>
        </div>

        {/* Command Groups & Items */}
        <div
          ref={listRef}
          className="flex-1 overflow-y-auto p-2 space-y-3 [scrollbar-width:thin] [scrollbar-color:rgba(255,255,255,0.1)_transparent]"
        >
          {filtered.length === 0 && (
            <div className="flex flex-col items-center justify-center py-10 text-center text-zinc-500 text-xs space-y-1">
              <Search className="w-6 h-6 opacity-40 mb-1" />
              <span>No commands or results found</span>
            </div>
          )}

          {Object.entries(groups).map(([group, items]) => (
            <div key={group} className="space-y-1">
              <div className="px-2 py-1 text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">
                {group}
              </div>
              <div className="space-y-0.5">
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
                      className={`w-full flex items-center justify-between px-3 py-2 rounded-xl text-xs transition-colors text-left ${
                        isActive
                          ? "bg-zinc-800 text-zinc-100 font-medium"
                          : "text-zinc-300 hover:bg-zinc-850/60"
                      }`}
                    >
                      <div className="flex items-center gap-2.5 truncate">
                        <Sparkles
                          className={`w-3.5 h-3.5 shrink-0 ${
                            isActive ? "text-violet-400" : "text-zinc-500"
                          }`}
                        />
                        <span className="truncate">{item.label}</span>
                        {item.hint && (
                          <span className="text-[11px] text-zinc-500 truncate">
                            {item.hint}
                          </span>
                        )}
                      </div>

                      <div className="flex items-center gap-2 shrink-0 ml-2">
                        {item.shortcut && (
                          <kbd className="px-1.5 py-0.5 text-[10px] font-mono text-zinc-400 bg-zinc-850 rounded border border-zinc-750">
                            {item.shortcut}
                          </kbd>
                        )}
                        {isActive && (
                          <CornerDownLeft className="w-3 h-3 text-zinc-400" />
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        {/* Footer shortcuts info */}
        <div className="flex items-center justify-between px-4 py-2 border-t border-zinc-800/80 bg-zinc-950/50 text-[11px] text-zinc-500">
          <span>Navigate with ↑ ↓</span>
          <span>Press Enter to select</span>
        </div>
      </div>
    </div>
  );
}
