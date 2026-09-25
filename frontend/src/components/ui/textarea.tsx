import * as React from "react";
import { cn } from "@/lib/utils";

export interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  error?: boolean;
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, error, ...props }, ref) => {
    return (
      <textarea
        className={cn(
          "flex min-h-[60px] w-full rounded-md border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-xs text-zinc-100 shadow-sm placeholder:text-zinc-500 transition-colors focus-visible:outline-none focus-visible:border-violet-500/80 focus-visible:ring-1 focus-visible:ring-violet-500/30 disabled:cursor-not-allowed disabled:opacity-50",
          error && "border-rose-500/80 focus-visible:border-rose-500 focus-visible:ring-rose-500/30",
          className
        )}
        ref={ref}
        {...props}
      />
    );
  }
);
Textarea.displayName = "Textarea";

export { Textarea };
