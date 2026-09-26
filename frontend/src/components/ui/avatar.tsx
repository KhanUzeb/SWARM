import * as React from "react";
import { cn } from "@/lib/utils";

export interface AvatarProps extends React.HTMLAttributes<HTMLDivElement> {
  name?: string;
  kind?: "human" | "agent" | "system";
  size?: "xs" | "sm" | "md" | "lg" | "xl";
  src?: string;
  avatar?: string;
}

const SIZE: Record<string, string> = {
  xs: "avatar-sm",
  sm: "avatar-sm",
  md: "",
  lg: "avatar-lg",
  xl: "avatar-lg",
};

const KIND: Record<string, string> = {
  human: "avatar-user",
  agent: "avatar-agent",
  system: "avatar-system",
};

/** A machined square with one consistent stroke, not a gradient bubble. */
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

  if (isImage) {
    return (
      <img
        src={pfp}
        alt={name}
        loading="lazy"
        className={cn("avatar", SIZE[size], className)}
      />
    );
  }

  // A teammate's own chosen mark, when they set one. This is user content
  // in a machined square, not part of the interface's icon system.
  if (pfp) {
    return (
      <div
        className={cn("avatar", SIZE[size], KIND[kind], className)}
        aria-label={name}
        role="img"
        {...props}
      >
        {pfp}
      </div>
    );
  }

  // Otherwise two letters, always.
  const initials = (name || "?")
    .split(/[\s-]+/)
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  return (
    <div
      className={cn("avatar", SIZE[size], KIND[kind], className)}
      aria-label={name}
      role="img"
      {...props}
    >
      {initials}
    </div>
  );
}
