import { Handle, HandleProps } from "@xyflow/react";
import { forwardRef } from "react";

import { cn } from "@/lib/utils";

export type BaseHandleProps = HandleProps;

export const BaseHandle = forwardRef<HTMLDivElement, BaseHandleProps>(
    ({ className, children, type, ...props }, ref) => {
        const isSource = type === 'source';
        const isTarget = type === 'target';

        return (
            <Handle
                ref={ref}
                type={type}
                {...props}
                className={cn(
                    "transition-colors hover:!bg-teal-600 !border-2 !border-background",
                    // Source (outgoing) has larger visible handle for easier connection
                    isSource && "!h-[14px] !w-[14px] !rounded-full",
                    // Target (incoming) smaller rectangle
                    isTarget && "!h-[14px] !w-[14px] !rounded-sm",
                    className,
                )}
                style={{
                    border: '2px solid var(--background)',
                    background: '#94A3B8', // slate-400
                    ...props.style,
                }}
            >
                {children}
            </Handle>
        );
    },
);

BaseHandle.displayName = "BaseHandle";
