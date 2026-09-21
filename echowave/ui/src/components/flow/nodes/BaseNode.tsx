import { forwardRef, HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export const BaseNode = forwardRef<
    HTMLDivElement,
    HTMLAttributes<HTMLDivElement> & {
        selected?: boolean;
        invalid?: boolean;
        selected_through_edge?: boolean;
        hovered_through_edge?: boolean;
        runtimeActive?: boolean;
    }
>(({ children, className, selected, invalid, selected_through_edge, hovered_through_edge, runtimeActive, ...props }, ref) => (
    <div
        ref={ref}
        className={cn(
            // Compact canvas card; full configuration belongs in the inspector.
            "relative w-[224px] rounded-xl border bg-card text-card-foreground shadow-sm transition-[border-color,box-shadow]",
            // Border styling
            "border-border",
            className,
            // Selection stays distinct from runtime and validation states.
            selected ? "border-teal-600 ring-2 ring-teal-600/20" : "",
            // Invalid state
            invalid ? "border-destructive ring-2 ring-destructive/20" : "",
            // Hovered through edge takes precedence over selected through edge
            hovered_through_edge ? "ring-2 ring-teal-600/40" : "",
            !hovered_through_edge && selected_through_edge ? "ring-1 ring-teal-600/30" : "",
            runtimeActive ? "ring-2 ring-sky-400/60 shadow-[0_0_0_1px_rgba(56,189,248,0.18),0_0_24px_rgba(14,165,233,0.18)]" : "",
            !selected_through_edge && !hovered_through_edge && "hover:border-muted-foreground/50",
        )}
        tabIndex={0}
        {...props}
    >
        {children}
    </div>
));

BaseNode.displayName = "BaseNode";
