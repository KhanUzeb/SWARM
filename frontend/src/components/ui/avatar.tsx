import * as React from "react";
import { cn } from "@/lib/utils";

export interface AvatarProps extends React.HTMLAttributes<HTMLDivElement> {
  name?: string;
  kind?: "human" | "agent" | "system";
  size?: "xs" | "sm" | "md" | "lg" | "xl";
  src?: string;
  avatar?: string;
}

export function Avatar({
  name = "User",
  kind = "human",
  size = "md",
  src,
  avatar,
  className,
  ...props
}: AvatarProps) {
  const pfp = (avatar || src || "").trim();
  const isImage = /^https?:\/\//i.test(pfp) || pfp.startsWith("data:image");

  const sizeClasses = {
    xs: "h-5 w-5 text-[9px]",
    sm: "h-6 w-6 text-[10px]",
    md: "h-8 w-8 text-xs",
    lg: "h-10 w-10 text-sm",
    xl: "h-12 w-12 text-base",
  }[size];

  const kindClasses = {
    human: "bg-zinc-800 text-zinc-200 border-zinc-700/60",
    agent: "bg-gradient-to-br from-violet-600/80 to-indigo-700/80 text-white border-violet-500/40 shadow-sm shadow-violet-950/40",
    system: "bg-zinc-850 text-zinc-400 border-zinc-750",
  }[kind];

  const initials = (name || "?")
    .split(/[\s-]+/)
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  if (isImage) {
    return (
      <img
        src={pfp}
        alt={name}
        loading="lazy"
        className={cn(
          "rounded-md object-cover border border-zinc-800/80 shrink-0",
          sizeClasses,
          className
        )}
      />
    );
  }

  if (pfp) {
    return (
      <div
        className={cn(
          "inline-flex items-center justify-center rounded-md border border-zinc-800/60 bg-zinc-900 select-none shrink-0",
          sizeClasses,
          className
        )}
        aria-label={name}
        role="img"
        {...props}
      >
        {pfp}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "inline-flex items-center justify-center rounded-md border font-semibold select-none shrink-0 tracking-tight",
        sizeClasses,
        kindClasses,
        className
      )}
      aria-label={name}
      {...props}
    >
      {initials}
    </div>
  );
}
