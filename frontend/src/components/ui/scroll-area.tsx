import * as React from "react";
import { cn } from "@/lib/utils";

export interface ScrollAreaProps extends React.HTMLAttributes<HTMLDivElement> {}

export function ScrollArea({ children, className, ...props }: ScrollAreaProps) {
  return (
    <div
      className={cn(
        "overflow-y-auto [scrollbar-width:thin] [scrollbar-color:rgba(255,255,255,0.12)_transparent]",
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
}
