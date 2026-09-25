import * as React from "react";
import { cn } from "@/lib/utils";

export interface TabsListProps extends React.HTMLAttributes<HTMLDivElement> {}

export function TabsList({ className, ...props }: TabsListProps) {
  return (
    <div
      className={cn(
        "inline-flex h-8 items-center justify-center rounded-lg bg-zinc-900/80 p-0.5 text-zinc-400 border border-zinc-800/80 backdrop-blur-sm",
        className
      )}
      role="tablist"
      {...props}
    />
  );
}

export interface TabTriggerProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean;
}

export function TabTrigger({
  className,
  active,
  children,
  ...props
}: TabTriggerProps) {
  return (
    <button
      role="tab"
      aria-selected={active}
      className={cn(
        "inline-flex items-center justify-center whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium transition-all focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 select-none",
        active
          ? "bg-zinc-800 text-zinc-100 shadow-sm font-semibold"
          : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-850/50",
        className
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export function ScrollArea({
  children,
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "overflow-y-auto overflow-x-hidden [scrollbar-width:thin] [scrollbar-color:rgba(255,255,255,0.12)_transparent]",
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
}
