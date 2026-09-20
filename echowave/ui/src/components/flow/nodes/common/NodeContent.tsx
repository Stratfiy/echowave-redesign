import { Position } from "@xyflow/react";
import { ReactNode } from "react";

import { BaseHandle } from "@/components/flow/nodes/BaseHandle";
import { BaseNode } from "@/components/flow/nodes/BaseNode";

interface NodeContentProps {
    selected: boolean;
    invalid?: boolean;
    selected_through_edge?: boolean;
    hovered_through_edge?: boolean;
    runtimeActive?: boolean;
    title: string;
    icon: ReactNode;
    badgeLabel?: string;
    badgeClassName?: string;
    contentLabel?: string;
    hasSourceHandle?: boolean;
    hasTargetHandle?: boolean;
    children?: ReactNode;
    className?: string;
    onDoubleClick?: () => void;
    nodeId?: string;
}

// Type labels stay neutral; runtime and validation carry state colours.

export const NodeContent = ({
    selected,
    invalid,
    selected_through_edge,
    hovered_through_edge,
    runtimeActive,
    title,
    icon,
    badgeLabel,
    contentLabel = "Prompt",
    hasSourceHandle = false,
    hasTargetHandle = false,
    children,
    className = "",
    onDoubleClick,
    nodeId,
}: NodeContentProps) => {
    const badgeText = badgeLabel ?? 'Node';

    return (
        <BaseNode
            selected={selected}
            invalid={invalid}
            selected_through_edge={selected_through_edge}
            hovered_through_edge={hovered_through_edge}
            runtimeActive={runtimeActive}
            className={`p-0 ${className}`}
            onDoubleClick={onDoubleClick}
        >
            {hasTargetHandle && <BaseHandle type="target" position={Position.Left} />}

            {/* Node type badge - positioned at top */}
            <div className="px-3 pt-3">
                <span className="inline-flex items-center gap-1.5 rounded border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
                    <span className="[&>*]:w-3 [&>*]:h-3">{icon}</span>
                    {badgeText}
                </span>
            </div>

            {/* Header with title */}
            <div className="px-3 pt-2 pb-2">
                <div className="flex items-center justify-between">
                    <h3 className="min-w-0 text-sm font-semibold text-foreground truncate" title={title}>
                        {title}
                        {nodeId && (
                            <span className="sr-only">
                                #{nodeId}
                            </span>
                        )}
                    </h3>
                </div>
            </div>

            {/* Content area with prompt label */}
            <div className="px-3 pb-3 text-xs [&_p]:line-clamp-3">
                <div className="text-[10px] text-muted-foreground mb-1 font-medium">
                    {contentLabel}:
                </div>
                {children}
            </div>

            {hasSourceHandle && <BaseHandle type="source" position={Position.Right} />}
        </BaseNode>
    );
};
