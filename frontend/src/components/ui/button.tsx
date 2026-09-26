import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * The control grammar lives in styles.css (`.btn` and friends) so the
 * shadcn-derived components and the classname-driven panels resolve to
 * one system instead of two that drift.
 */
const VARIANT: Record<string, string> = {
  default: "",
  primary: "btn-primary",
  secondary: "",
  outline: "",
  ghost: "btn-ghost",
  destructive: "btn-danger",
  subtle: "",
};

const SIZE: Record<string, string> = {
  default: "",
  sm: "btn-sm",
  lg: "",
  icon: "btn-icon",
  "icon-sm": "btn-icon btn-sm",
  "icon-xs": "btn-icon btn-sm",
};

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof VARIANT;
  size?: keyof typeof SIZE;
  loading?: boolean;
  icon?: React.ReactNode;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { className, variant = "secondary", size = "default", loading, icon, children, disabled, ...props },
    ref,
  ) => (
    <button
      ref={ref}
      className={cn("btn", VARIANT[variant], SIZE[size], className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? <span className="spinner spinner-sm" aria-hidden="true" /> : icon}
      {children}
    </button>
  ),
);
Button.displayName = "Button";

export { Button };
export const buttonVariants = ({ variant, size }: { variant?: string; size?: string } = {}) =>
  cn("btn", VARIANT[variant ?? ""], SIZE[size ?? ""]);
