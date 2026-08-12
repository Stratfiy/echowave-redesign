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
            // Base styling - larger with max width, uses semantic colors
            "relative rounded-lg border bg-card text-card-foreground min-w-[320px] max-w-[400px] min-h-[120px]",
            // Border styling
            "border-border",
            className,
            // Selection states. The rings were already token-driven, but each
            // one also carried a hand-written blue halo — so a selected node
            // rendered an orange ring inside a blue glow. The halos are gone
            // rather than recoloured: the ring is the affordance, and glows are
            // not part of this design language.
            selected ? "border-primary ring-2 ring-primary/40" : "",
            invalid ? "border-destructive ring-2 ring-destructive/30" : "",
            // Hovered through edge takes precedence over selected through edge
            hovered_through_edge ? "ring-2 ring-primary/60" : "",
            !hovered_through_edge && selected_through_edge ? "ring-1 ring-primary/50" : "",
            // Live execution keeps its own colour: this signals runtime state,
            // not selection, and must not be mistaken for the accent.
            runtimeActive ? "ring-2 ring-sky-400/60" : "",
            !selected_through_edge && !hovered_through_edge && "hover:border-muted-foreground/50",
        )}
        tabIndex={0}
        {...props}
    >
        {children}
    </div>
));

BaseNode.displayName = "BaseNode";
