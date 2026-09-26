import * as React from "react";
import { cn } from "@/lib/utils";
import { placeOverlay, type OverlayPlacement } from "@/lib/overlay";

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
  const [placement, setPlacement] = React.useState<OverlayPlacement | null>(null);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const menuRef = React.useRef<HTMLDivElement>(null);

  const position = React.useCallback(() => {
    if (!containerRef.current || !menuRef.current) return;
    setPlacement(
      placeOverlay(containerRef.current.getBoundingClientRect(), menuRef.current, {
        align,
        minHeight: 90,
      }),
    );
  }, [align]);

  React.useLayoutEffect(() => {
    if (!open) {
      setPlacement(null);
      return undefined;
    }
    position();
    function onReflow() {
      position();
    }
    window.addEventListener("resize", onReflow);
    window.addEventListener("scroll", onReflow, true);
    return () => {
      window.removeEventListener("resize", onReflow);
      window.removeEventListener("scroll", onReflow, true);
    };
  }, [open, position]);

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
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className={cn("inline-block text-left", className)} ref={containerRef}>
      <span onClick={() => setOpen((p) => !p)} className="inline-flex cursor-pointer">
        {trigger}
      </span>

      {open && (
        <div
          ref={menuRef}
          role="menu"
          aria-label={label}
          style={
            placement
              ? { top: placement.top, left: placement.left, maxHeight: placement.maxHeight }
              : { top: 0, left: 0, visibility: "hidden" }
          }
          className="dropdown-menu"
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
