import { cva, type VariantProps } from "class-variance-authority"
import * as React from "react"

import { cn } from "@/lib/utils"

// Badges go the other way from buttons: 6px, not a pill. Two fully-rounded
// shapes at different sizes read as the same control, and a status badge is not
// something you click — the shape difference is what says so.
const badgeVariants = cva(
    "inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
    {
        variants: {
            variant: {
                default:
                    "border-transparent bg-primary text-primary-foreground hover:bg-primary/80",
                secondary:
                    "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",
                destructive:
                    "border-transparent bg-destructive text-destructive-foreground hover:bg-destructive/80",
                outline: "text-foreground",
                // Semantics get their own tones rather than a Tailwind default.
                // green-500 was a raw palette value that answered to nothing.
                success:
                    "border-transparent bg-emerald-600 text-white hover:bg-emerald-700",
                warning:
                    "border-transparent bg-warning-surface text-warning-foreground hover:bg-warning/25",
            },
        },
        defaultVariants: {
            variant: "default",
        },
    }
)

export interface BadgeProps
    extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> { }

const Badge = React.forwardRef<HTMLDivElement, BadgeProps>(
    ({ className, variant, ...props }, ref) => {
        return (
            <div
                ref={ref}
                className={cn(badgeVariants({ variant }), className)}
                {...props}
            />
        )
    }
)
Badge.displayName = "Badge"

export { Badge, badgeVariants }
