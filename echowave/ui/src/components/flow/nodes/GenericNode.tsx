import { NodeProps, NodeToolbar, Position } from "@xyflow/react";
import * as LucideIcons from "lucide-react";
import { AlertCircle, Circle, Edit, type LucideIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { memo, useCallback, useEffect, useMemo, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import type { NodeSpec } from "@/client/types.gen";
import { NodeEditForm, useNodeSpecs } from "@/components/flow/renderer";
import { FlowNodeData } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { NODE_DOCUMENTATION_URLS } from "@/constants/documentation";
import { useAppConfig } from "@/context/AppConfigContext";
import { cn } from "@/lib/utils";
import { resolveWebhookBaseUrl } from "@/lib/webhookUrl";

import { BaseHandle } from "./BaseHandle";
import { BaseNode } from "./BaseNode";
import { NodeEditDialog } from "./common/NodeEditDialog";
import { useNodeHandlers } from "./common/useNodeHandlers";
import { NodeHeader, NodeHeaderIcon, NodeHeaderTitle } from "./NodeHeader";

// ─── Static per-spec UI maps ──────────────────────────────────────────────
// Small lookups indexed by spec.name. Keeping these in the renderer (not
// the spec) avoids leaking UI concerns into the backend schema. Add an
// entry when registering a new node type.

const HANDLES_BY_SPEC: Record<string, { source: boolean; target: boolean }> = {
    startCall: { source: true, target: false },
    agentNode: { source: true, target: true },
    endCall: { source: false, target: true },
    globalNode: { source: false, target: false },
    trigger: { source: false, target: false },
    webhook: { source: false, target: false },
    qa: { source: false, target: false },
    branch: { source: true, target: true },
    wait: { source: true, target: true },
    sms: { source: false, target: false },
};

const DOC_URL_BY_SPEC: Record<string, string | undefined> = {
    startCall: NODE_DOCUMENTATION_URLS.startCall,
    agentNode: NODE_DOCUMENTATION_URLS.agent,
    endCall: NODE_DOCUMENTATION_URLS.endCall,
    globalNode: NODE_DOCUMENTATION_URLS.global,
    trigger: NODE_DOCUMENTATION_URLS.apiTrigger,
    webhook: NODE_DOCUMENTATION_URLS.webhook,
    qa: NODE_DOCUMENTATION_URLS.qaAnalysis,
    branch: NODE_DOCUMENTATION_URLS.branch,
    wait: NODE_DOCUMENTATION_URLS.wait,
    sms: NODE_DOCUMENTATION_URLS.sms,
};

// ─── Helpers ──────────────────────────────────────────────────────────────

function resolveIcon(name: string): LucideIcon {
    const icons = LucideIcons as unknown as Record<string, LucideIcon>;
    return icons[name] ?? Circle;
}

function seedValues(
    data: FlowNodeData,
    spec: NodeSpec,
): Record<string, unknown> {
    const d = data as unknown as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const prop of spec.properties) {
        out[prop.name] = d[prop.name] ?? prop.default ?? undefined;
    }
    return out;
}

interface TriggerEndpoints {
    production: string;
    test: string;
}

function buildTriggerEndpoints(
    triggerPath: string | undefined,
    baseUrl: string,
): TriggerEndpoints {
    if (!triggerPath) return { production: "", test: "" };
    return {
        production: `${baseUrl}/api/v1/public/agent/${triggerPath}`,
        test: `${baseUrl}/api/v1/public/agent/test/${triggerPath}`,
    };
}

function resolveIntegrationEnabled(
    spec: NodeSpec,
    data: FlowNodeData,
): boolean {
    for (const prop of spec.properties) {
        if (!prop.name.endsWith("enabled")) continue;
        const value = data[prop.name];
        if (typeof value === "boolean") return value;
    }
    return true;
}

// ─── Trigger webhook URLs (test + production) — rendered inside the dialog ─

function buildCurl(endpoint: string): string {
    return `curl -X POST "${endpoint}" \\
  -H "X-API-Key: YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"phone_number": "+1234567890", "initial_context": {}}'`;
}

function ClickToCopy({
    value,
    children,
    className,
    title,
}: {
    value: string;
    children: React.ReactNode;
    className?: string;
    title?: string;
}) {
    const [copied, setCopied] = useState(false);
    const onCopy = async () => {
        if (!value) return;
        await navigator.clipboard.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };
    return (
        <button
            type="button"
            onClick={onCopy}
            title={title ?? "Click to copy"}
            className={cn(
                "group relative text-left transition-colors hover:bg-accent/60 cursor-pointer disabled:cursor-default",
                className,
            )}
            disabled={!value}
        >
            {children}
            <span
                aria-hidden={!copied}
                className={cn(
                    "pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded bg-foreground/90 px-1.5 py-0.5 text-[10px] font-medium text-background shadow transition-opacity",
                    copied ? "opacity-100" : "opacity-0",
                )}
            >
                Copied!
            </span>
        </button>
    );
}

function UrlPanel({
    endpoint,
    helperText,
}: {
    endpoint: string;
    helperText: string;
}) {
    const curl = endpoint ? buildCurl(endpoint) : "";
    return (
        <div className="grid gap-2 pt-2">
            <div className="flex items-center gap-2">
                <span className="text-xs font-mono bg-muted px-1.5 py-0.5 rounded shrink-0">
                    POST
                </span>
                <ClickToCopy
                    value={endpoint}
                    title="Click to copy URL"
                    className="flex-1 bg-muted rounded px-2 py-1"
                >
                    <code className="text-xs break-all">
                        {endpoint || "Generating..."}
                    </code>
                </ClickToCopy>
            </div>
            <p className="text-xs text-muted-foreground">{helperText}</p>
            <p className="text-sm font-medium pt-2">Example Request</p>
            <ClickToCopy
                value={curl}
                title="Click to copy curl"
                className="block w-full bg-muted rounded"
            >
                <pre className="text-xs px-3 py-2 overflow-x-auto whitespace-pre-wrap">
                    {curl || "Generating..."}
                </pre>
            </ClickToCopy>
        </div>
    );
}

function TriggerWebhookUrls({ endpoints }: { endpoints: TriggerEndpoints }) {
    return (
        <div className="grid gap-2">
            <p className="text-sm font-medium">Webhook URLs</p>
            <p className="text-xs text-muted-foreground">
                Test mode runs the latest draft so you can verify changes before
                publishing. Production runs the published agent. Both require an
                API key in the X-API-Key header.{" "}
                <Link
                    href="/api-keys"
                    target="_blank"
                    className="text-primary underline hover:no-underline"
                >
                    Get your API key
                </Link>
            </p>
            <Tabs defaultValue="test" className="w-full">
                <TabsList>
                    <TabsTrigger value="test">Test URL</TabsTrigger>
                    <TabsTrigger value="production">Production URL</TabsTrigger>
                </TabsList>
                <TabsContent value="test">
                    <UrlPanel
                        endpoint={endpoints.test}
                        helperText="Runs the latest draft, falling back to the published bot when no draft exists."
                    />
                </TabsContent>
                <TabsContent value="production">
                    <UrlPanel
                        endpoint={endpoints.production}
                        helperText="Runs the published bot."
                    />
                </TabsContent>
            </Tabs>
        </div>
    );
}

// ─── GenericNode ──────────────────────────────────────────────────────────

interface GenericNodeProps extends NodeProps {
    data: FlowNodeData;
    type: string;
}

export const GenericNode = memo(({ data, selected, id, type }: GenericNodeProps) => {
    // Per-type metadata that StartCall/EndCall used to set via `additionalData`
    // (is_start / is_end). Pulled from the spec name here.
    const additionalData = useMemo<Record<string, boolean> | undefined>(() => {
        const out: Record<string, boolean> = {};
        if (type === "startCall") out.is_start = true;
        if (type === "endCall") out.is_end = true;
        return Object.keys(out).length > 0 ? out : undefined;
    }, [type]);

    const { open, setOpen, handleSaveNodeData, handleDeleteNode } = useNodeHandlers({
        id,
        additionalData,
    });
    const { saveWorkflow, tools, documents, recordings, workflowUuid, readOnly } = useWorkflow();
    const { bySpecName } = useNodeSpecs();
    const { config: appConfig } = useAppConfig();
    const spec = bySpecName.get(type);
    const webhookBaseUrl = resolveWebhookBaseUrl(appConfig?.tunnelUrl);

    // ── Form state ─────────────────────────────────────────────────────
    // mcp_tool_filters is not a spec property, so seedValues won't carry it;
    // seed merges it back in alongside the spec-derived values.
    const seed = useCallback(
        () =>
            spec
                ? { ...seedValues(data, spec), mcp_tool_filters: data.mcp_tool_filters }
                : {},
        [data, spec],
    );

    const [values, setValues] = useState<Record<string, unknown>>(seed);

    // Re-seed once the spec arrives (initial fetch race).
    useEffect(() => {
        if (spec && Object.keys(values).length === 0) {
            setValues(seed());
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [spec]);

    // ── Dirty / save / open handlers ────────────────────────────────────
    const propertyNames = useMemo(
        () => spec?.properties.map((p) => p.name) ?? [],
        [spec],
    );

    const isDirty = useMemo(() => {
        if (!spec) return false;
        const baseline = seedValues(data, spec);
        if (propertyNames.some((n) => values[n] !== baseline[n])) return true;
        return (
            JSON.stringify(values.mcp_tool_filters ?? {}) !==
            JSON.stringify(data.mcp_tool_filters ?? {})
        );
    }, [values, data, spec, propertyNames]);

    const handleSave = async () => {
        if (!spec || readOnly) return;
        handleSaveNodeData({
            ...data,
            ...(values as Partial<FlowNodeData>),
        });
        setOpen(false);
        await saveWorkflow();
    };

    const handleOpenChange = (newOpen: boolean) => {
        if (newOpen && spec) setValues(seed());
        setOpen(newOpen);
    };

    useEffect(() => {
        if (open && spec) setValues(seed());
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [data, open]);

    // ── Render ──────────────────────────────────────────────────────────
    const handles =
        HANDLES_BY_SPEC[type] ??
        (spec?.category === "integration"
            ? { source: false, target: false }
            : { source: true, target: true });
    const Icon = spec ? resolveIcon(spec.icon) : Circle;
    const docUrl = spec?.docs_url ?? DOC_URL_BY_SPEC[type];
    // Edit dialog title: "Edit {display_name}". Webhook keeps the original
    // "Edit Webhook" wording — display_name is "Webhook" so it works out.
    const dialogTitle = spec ? `Edit ${spec.display_name}` : "Edit Node";
    const fallbackTitle = spec?.display_name ?? "Node";

    return (
        <>
            <BaseNode
                selected={selected} invalid={data.invalid}
                selected_through_edge={data.selected_through_edge}
                hovered_through_edge={data.hovered_through_edge}
                runtimeActive={data.runtime_active}
                onDoubleClick={() => handleOpenChange(true)}
                onKeyDown={(event) => { if (event.key === "Enter" && event.target === event.currentTarget) { event.preventDefault(); handleOpenChange(true); } }}
                aria-label={`${data.name || fallbackTitle}, ${fallbackTitle}. Press Enter to inspect.`}
            >
                {handles.target && <BaseHandle type="target" position={Position.Left} aria-label="Input connection" />}
                <NodeHeader>
                    <NodeHeaderIcon><Icon aria-hidden="true" /></NodeHeaderIcon>
                    <div className="min-w-0 flex-1">
                        <NodeHeaderTitle title={data.name || fallbackTitle}>{data.name || fallbackTitle}</NodeHeaderTitle>
                        <p className="mt-1 truncate text-[11px] text-muted-foreground">{fallbackTitle}</p>
                    </div>
                    {data.invalid ? <AlertCircle aria-label="Needs attention" className="h-4 w-4 shrink-0 text-destructive" />
                        : data.runtime_active ? <span aria-label="Running" className="h-2.5 w-2.5 shrink-0 animate-pulse rounded-full bg-teal-500" />
                        : spec?.category === "integration" && !resolveIntegrationEnabled(spec, data) ? <span className="shrink-0 text-[10px] text-muted-foreground">Off</span> : null}
                </NodeHeader>
                {handles.source && <BaseHandle type="source" position={Position.Right} aria-label="Output connection" />}
            </BaseNode>

            <NodeToolbar isVisible={selected} position={Position.Top}>
                <div className="flex gap-1">
                    <Button aria-label={`Edit ${data.name || fallbackTitle}`} onClick={() => setOpen(true)} variant="outline" size="icon">
                        <Edit />
                    </Button>
                    {/* Start nodes can't be deleted (workflow always needs one). */}
                    {type !== "startCall" && !readOnly && (
                        <Button
                            onClick={handleDeleteNode}
                            aria-label={`Delete ${data.name || fallbackTitle}`}
                            variant="outline"
                            size="icon"
                        >
                            <Trash2Icon />
                        </Button>
                    )}
                </div>
            </NodeToolbar>

            <NodeEditDialog
                open={open}
                onOpenChange={handleOpenChange}
                nodeData={data}
                title={dialogTitle}
                onSave={handleSave}
                isDirty={isDirty}
                documentationUrl={docUrl}
            >
                {open && spec && (
                    <div className="grid gap-4">
                        <NodeEditForm
                            spec={spec}
                            values={values}
                            onChange={setValues}
                            context={{
                                tools: tools ?? [],
                                documents: documents ?? [],
                                recordings: recordings ?? [],
                                workflowUuid,
                                mcpToolFilters:
                                    (values.mcp_tool_filters as
                                        | Record<string, string[]>
                                        | undefined) ?? {},
                                onMcpToolFiltersChange: (next) =>
                                    setValues((prev) => ({
                                        ...prev,
                                        mcp_tool_filters:
                                            Object.keys(next).length > 0
                                                ? next
                                                : undefined,
                                    })),
                            }}
                        />
                        {type === "trigger" && (
                            <TriggerWebhookUrls
                                endpoints={buildTriggerEndpoints(data.trigger_path, webhookBaseUrl)}
                            />
                        )}
                    </div>
                )}
            </NodeEditDialog>
        </>
    );
});

GenericNode.displayName = "GenericNode";
