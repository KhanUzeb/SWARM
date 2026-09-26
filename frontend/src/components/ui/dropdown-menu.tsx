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
  const [placement, setPlacement] = React.useState<"top" | "bottom">("bottom");
  const containerRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    const rect = containerRef.current?.getBoundingClientRect();
    if (rect) {
      setPlacement(
        window.innerHeight - rect.bottom < 190 && rect.top > 190 ? "top" : "bottom",
      );
    }
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className={cn("relative inline-block text-left", className)} ref={containerRef}>
      <span onClick={() => setOpen((p) => !p)} className="inline-flex cursor-pointer">
        {trigger}
      </span>

      {open && (
        <div
          role="menu"
          aria-label={label}
          style={placement === "top" ? { bottom: "calc(100% + 6px)" } : { top: "calc(100% + 6px)" }}
          className={cn("dropdown-menu", align === "right" ? "right-0" : "left-0")}
        >
          {items.map((item, i) => {
            if (item === "divider") return <div key={`d${i}`} className="dropdown-divider" />;
            if (item.section) {
              return (
                <div key={`s${i}`} className="dropdown-section reg">
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
                className={cn("dropdown-item", item.danger && "danger")}
              >
                {item.icon && <span className="shrink-0">{item.icon}</span>}
                <span className="flex-1 truncate">{item.label}</span>
                {item.shortcut && <span className="reg">{item.shortcut}</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
