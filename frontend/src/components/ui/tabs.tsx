import * as React from "react";
import { cn } from "@/lib/utils";

export interface TabsListProps extends React.HTMLAttributes<HTMLDivElement> {}

export function TabsList({ className, ...props }: TabsListProps) {
  return <div className={cn("view-tabs", className)} role="tablist" {...props} />;
}

export interface TabTriggerProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean;
}

export function TabTrigger({ className, active, children, ...props }: TabTriggerProps) {
  return (
    <button
      role="tab"
      aria-selected={active}
      className={cn("view-tab", active && "active", className)}
      {...props}
    >
      {children}
    </button>
  );
}
