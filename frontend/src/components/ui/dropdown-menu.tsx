import * as React from "react";
import { cn } from "@/lib/utils";

export interface DropdownMenuItem {
  id?: string;
  label?: React.ReactNode;
  icon?: React.ReactNode;
  shortcut?: string;
  danger?: boolean;
  disabled?: boolean;
  section?: string;
  onClick?: () => void;
}

export type DropdownItemType = DropdownMenuItem | "divider";

export interface DropdownProps {
  trigger: React.ReactNode;
  items: DropdownItemType[];
  align?: "left" | "right";
  label?: string;
  className?: string;
}

export function Dropdown({
  trigger,
  items,
  align = "right",
  label = "Menu",
  className,
}: DropdownProps) {
  const [open, setOpen] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
      document.addEventListener("keydown", handleKeyDown);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  return (
    <div className={cn("relative inline-block text-left", className)} ref={containerRef}>
      <span
        role="button"
        tabIndex={0}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        onClick={() => setOpen((prev) => !prev)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen((prev) => !prev);
          }
        }}
        className="inline-flex cursor-pointer"
      >
        {trigger}
      </span>

      {open && (
        <div
          role="menu"
          aria-label={label}
          className={cn(
            "absolute z-50 mt-1.5 min-w-[160px] overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900/95 p-1 text-zinc-200 shadow-xl backdrop-blur-md animate-in fade-in-0 zoom-in-95 duration-100",
            align === "right" ? "right-0" : "left-0"
          )}
        >
          {items.map((item, i) => {
            if (item === "divider") {
              return (
                <div
                  key={`div-${i}`}
                  className="my-1 h-px bg-zinc-800/80 -mx-1"
                />
              );
            }
            if (item.section) {
              return (
                <div
                  key={`sec-${i}`}
                  className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500"
                >
                  {item.section}
                </div>
              );
            }
            return (
              <button
                key={item.id || i}
                role="menuitem"
                disabled={item.disabled}
                onClick={() => {
                  item.onClick?.();
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs font-normal text-zinc-300 transition-colors hover:bg-zinc-800/80 hover:text-zinc-100 disabled:pointer-events-none disabled:opacity-40 text-left select-none",
                  item.danger &&
                    "text-rose-400 hover:bg-rose-500/10 hover:text-rose-300"
                )}
              >
                {item.icon && <span className="shrink-0">{item.icon}</span>}
                <span className="flex-1 truncate">{item.label}</span>
                {item.shortcut && (
                  <span className="ml-auto text-[10px] font-mono text-zinc-500">
                    {item.shortcut}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
