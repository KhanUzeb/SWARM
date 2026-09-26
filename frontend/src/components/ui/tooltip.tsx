import * as React from "react";
import { cn } from "@/lib/utils";

export interface TooltipProps {
  children: React.ReactNode;
  content: React.ReactNode;
  className?: string;
  side?: "top" | "bottom" | "left" | "right";
}

const EDGE: Record<string, string> = {
  top: "bottom-full left-1/2 -translate-x-1/2 mb-1.5",
  bottom: "top-full left-1/2 -translate-x-1/2 mt-1.5",
  left: "right-full top-1/2 -translate-y-1/2 mr-1.5",
  right: "left-full top-1/2 -translate-y-1/2 ml-1.5",
};

export function Tooltip({ children, content, className, side = "top" }: TooltipProps) {
  const [visible, setVisible] = React.useState(false);
  const timer = React.useRef<number | undefined>(undefined);

  const show = () => {
    clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setVisible(true), 150);
  };
  const hide = () => {
    clearTimeout(timer.current);
    setVisible(false);
  };

  React.useEffect(() => () => clearTimeout(timer.current), []);

  return (
    <div
      className={cn("relative inline-flex", className)}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      {visible && content && (
        <div role="tooltip" className={cn("tooltip", EDGE[side])}>
          {content}
        </div>
      )}
    </div>
  );
}
