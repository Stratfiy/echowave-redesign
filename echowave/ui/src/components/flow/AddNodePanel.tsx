import * as LucideIcons from 'lucide-react';
import { Circle, type LucideIcon, Search, X } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';

import type { NodeSpec } from '@/client/types.gen';
import { useNodeSpecs } from '@/components/flow/renderer';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

import { FlowNode, NodeType } from './types';

type AddNodePanelProps = {
    isOpen: boolean;
    onClose: () => void;
    onNodeSelect: (nodeType: NodeType) => void;
    nodes: FlowNode[];
};

// Section ordering and labels. Drives both the category → section title
// mapping and the rendering order.
const SECTION_ORDER: Array<{ category: NodeSpec['category']; title: string }> = [
    { category: 'trigger', title: 'Triggers' },
    { category: 'call_node', title: 'Conversation and actions' },
    { category: 'global_node', title: 'Agent configuration' },
    { category: 'integration', title: 'Integrations' },
];

function resolveIcon(name: string): LucideIcon {
    const icons = LucideIcons as unknown as Record<string, LucideIcon>;
    return icons[name] ?? Circle;
}

function NodeSection({
    title,
    specs,
    onNodeSelect,
    nodeTypeCounts,
}: {
    title: string;
    specs: NodeSpec[];
    onNodeSelect: (nodeType: NodeType) => void;
    nodeTypeCounts: Map<string, number>;
}) {
    if (specs.length === 0) return null;
    return (
        <div className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {title}
            </h3>
            <div className="space-y-2">
                {specs.map((spec) => {
                    const Icon = resolveIcon(spec.icon);
                    const maxInstances = spec.graph_constraints?.max_instances;
                    const disabled =
                        maxInstances !== undefined &&
                        maxInstances !== null &&
                        (nodeTypeCounts.get(spec.name) ?? 0) >= maxInstances;
                    return (
                        <Button
                            key={spec.name}
                            variant="outline"
                            className="w-full justify-start p-3 h-auto hover:border-teal-600/40 hover:bg-teal-500/5 transition-colors"
                            onClick={() => onNodeSelect(spec.name as NodeType)}
                            disabled={disabled}
                            aria-label={spec.display_name}
                            aria-description={disabled ? 'Already at the limit for this agent' : spec.description}
                            title={
                                disabled
                                    ? `${spec.display_name} limit reached for this workflow`
                                    : undefined
                            }
                        >
                            <div className="flex items-center">
                                <div className="bg-muted p-2 rounded-lg mr-3 shrink-0 border border-border">
                                    <Icon className="h-4 w-4" />
                                </div>
                                <div className="flex flex-col items-start text-left min-w-0">
                                    <span className="font-medium text-sm">
                                        {spec.display_name}
                                    </span>
                                    <span className="text-xs text-muted-foreground whitespace-normal line-clamp-2">
                                        {disabled ? 'Already at the limit for this agent' : spec.description}
                                    </span>
                                </div>
                            </div>
                        </Button>
                    );
                })}
            </div>
        </div>
    );
}

export default function AddNodePanel({ isOpen, onNodeSelect, onClose, nodes }: AddNodePanelProps) {
    const { specs } = useNodeSpecs();
    const [query, setQuery] = useState('');
    const searchRef = useRef<HTMLInputElement>(null);
    const titleId = useId();
    const searchId = useId();
    const normalizedQuery = query.trim().toLowerCase();

    // Group registered specs by category, preserving the SECTION_ORDER.
    // Adding a new node type with a new spec.category just shows up here.
    const sections = useMemo(() => {
        const groups = SECTION_ORDER.map(({ category, title }) => ({
            title,
            specs: specs.filter((s) => s.category === category && `${s.display_name} ${s.description} ${s.name} ${title}`.toLowerCase().includes(normalizedQuery)),
        }));
        const known = new Set(SECTION_ORDER.map(section => section.category));
        groups.push({ title: "Other nodes", specs: specs.filter(spec => !known.has(spec.category) && `${spec.display_name} ${spec.description} ${spec.name}`.toLowerCase().includes(normalizedQuery)) });
        return groups;
    }, [specs, normalizedQuery]);

    const nodeTypeCounts = useMemo(() => {
        const counts = new Map<string, number>();
        nodes.forEach((node) => {
            counts.set(node.type, (counts.get(node.type) ?? 0) + 1);
        });
        return counts;
    }, [nodes]);

    useEffect(() => {
        if (!isOpen) return;
        const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        setQuery('');
        searchRef.current?.focus();
        return () => {
            if (previousFocus?.isConnected) previousFocus.focus();
        };
    }, [isOpen]);

    // Closed panels must not leave off-screen controls in the tab order.
    if (!isOpen) return null;
    const resultCount = sections.reduce((count, section) => count + section.specs.length, 0);

    return (
        <aside
            aria-labelledby={titleId}
            className="nodrag nowheel absolute z-50 inset-x-0 bottom-0 flex max-h-[75%] w-full flex-col rounded-t-xl border-t border-border bg-background shadow-xl sm:left-auto sm:right-0 sm:top-0 sm:h-full sm:max-h-full sm:w-80 sm:rounded-none sm:border-l"
            onKeyDown={(event) => {
                if (event.key === 'Escape') {
                    event.stopPropagation();
                    onClose();
                }
            }}
        >
            <div className="border-b border-border p-4">
                <div className="mb-3 flex items-center justify-between">
                    <h2 id={titleId} className="text-base font-semibold">Add a node</h2>
                    <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close node picker">
                        <X className="size-4" />
                    </Button>
                </div>
                <label htmlFor={searchId} className="sr-only">Search nodes</label>
                <div className="relative">
                    <Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" />
                    <Input id={searchId} ref={searchRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search nodes…" className="pl-9" />
                </div>
                <p className="mt-2 text-xs text-muted-foreground" aria-live="polite">{resultCount} {resultCount === 1 ? 'node' : 'nodes'} available</p>
            </div>
            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-3">
                {sections.map(({ title, specs }) => (
                    <NodeSection key={title} title={title} specs={specs} onNodeSelect={onNodeSelect} nodeTypeCounts={nodeTypeCounts} />
                ))}
                {resultCount === 0 && <p className="px-2 py-6 text-sm text-muted-foreground">No matching nodes. Try a different name or action.</p>}
            </div>
        </aside>
    );
}
