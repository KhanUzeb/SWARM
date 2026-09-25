import * as React from "react";
import { cn } from "@/lib/utils";

export interface InputProps
  extends React.InputHTMLAttributes<HTMLInputElement> {
  error?: boolean;
}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, error, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          "flex h-8 w-full rounded-md border border-zinc-800 bg-zinc-950/60 px-3 py-1 text-xs text-zinc-100 shadow-sm transition-colors file:border-0 file:bg-transparent file:text-xs file:font-medium placeholder:text-zinc-500 focus-visible:outline-none focus-visible:border-violet-500/80 focus-visible:ring-1 focus-visible:ring-violet-500/30 disabled:cursor-not-allowed disabled:opacity-50",
          error && "border-rose-500/80 focus-visible:border-rose-500 focus-visible:ring-rose-500/30",
          className
        )}
        ref={ref}
        {...props}
      />
    );
  }
);
Input.displayName = "Input";

export { Input };
