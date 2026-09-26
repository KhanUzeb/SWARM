import * as React from "react";
import { cn } from "@/lib/utils";

const VARIANT: Record<string, string> = {
  default: "",
  neutral: "",
  brand: "badge-go",
  success: "badge-go",
  warning: "badge-amber",
  error: "badge-hold",
  subtle: "",
  outline: "",
};

const SIZE: Record<string, string> = {
  default: "",
  sm: "",
  lg: "",
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: keyof typeof VARIANT;
  size?: keyof typeof SIZE;
}

export function Badge({
  className,
  variant = "neutral",
  size = "default",
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn("badge", VARIANT[variant], SIZE[size], className)}
      {...props}
    />
  );
}

export const badgeVariants = ({ variant }: { variant?: string } = {}) =>
  cn("badge", VARIANT[variant ?? ""]);
