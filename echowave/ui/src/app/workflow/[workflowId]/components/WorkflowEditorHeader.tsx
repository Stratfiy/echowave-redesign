"use client";

import { ReactFlowInstance } from "@xyflow/react";
import { AlertCircle, ArrowLeft, BookmarkPlus, Bot, Clipboard, Copy, Download, Eye, History, LoaderCircle, Menu, MoreVertical, Pencil, Phone, Rocket } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";

import {
    duplicateWorkflowEndpointApiV1WorkflowWorkflowIdDuplicatePost,
    publishWorkflowApiV1WorkflowWorkflowIdPublishPost, updateWorkflowVisibilityApiV1WorkflowWorkflowIdVisibilityPut } from "@/client/sdk.gen";
import { WorkflowError } from "@/client/types.gen";
import { FlowEdge, FlowNode } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover";
import { useSidebar } from "@/components/ui/sidebar";
import { SaveAsRoleDialog } from "@/components/workflow/SaveAsRoleDialog";
import { useFeature } from "@/lib/features";

interface WorkflowEditorHeaderProps {
    workflowName: string;
    isDirty: boolean;
    workflowValidationErrors: WorkflowError[];
    rfInstance: React.RefObject<ReactFlowInstance<FlowNode, FlowEdge> | null>;
    workflowId: number;
    workflowUuid?: string;
    /** Who may see this agent (KAN-158): "everyone" or "admins". */
    visibility?: string;
    saveWorkflow: (updateWorkflowDefinition?: boolean) => Promise<void>;
    user: { id: string; email?: string };
    onPhoneCallClick: () => void;
    onTestAgentClick: () => void;
    onHistoryClick: () => void;
    activeVersionLabel?: string;
    isViewingHistoricalVersion: boolean;
    onBackToDraft: () => void;
    hasDraft: boolean;
    onPublished: () => void;
    renameWorkflow: (newName: string) => Promise<void>;
}

/** One clause the published instructions breach, as the API reports it. */
interface AcceptableUseFinding {
    clause: string;
    title: string;
    quote: string;
    why: string;
}

export const WorkflowEditorHeader = ({
    workflowName,
    isDirty,
    workflowValidationErrors,
    rfInstance,
    saveWorkflow,
    onPhoneCallClick,
    onTestAgentClick,
    onHistoryClick,
    activeVersionLabel,
    isViewingHistoricalVersion,
    onBackToDraft,
    hasDraft,
    onPublished,
    workflowId,
    workflowUuid,
    visibility: visibilityProp,
    renameWorkflow,
}: WorkflowEditorHeaderProps) => {
    const router = useRouter();
    const { toggleSidebar } = useSidebar();
    const [savingWorkflow, setSavingWorkflow] = useState(false);
    const [duplicating, setDuplicating] = useState(false);
    // MP-2: offered only while workspace roles are switched on.
    const rolesAvailable = useFeature("workspace_roles");
    const [savingRole, setSavingRole] = useState(false);
    // KAN-158: who may see this agent. Admins only hides it from members
    // everywhere a person is served; a run is not a person and still sees it.
    const [visibility, setVisibility] = useState(visibilityProp ?? "everyone");
    const [settingVisibility, setSettingVisibility] = useState(false);
    const toggleVisibility = async () => {
        const wanted = visibility === "admins" ? "everyone" : "admins";
        setSettingVisibility(true);
        try {
            const result = await updateWorkflowVisibilityApiV1WorkflowWorkflowIdVisibilityPut({
                path: { workflow_id: workflowId },
                body: { visibility: wanted },
            });
            if (!result.error) setVisibility(wanted);
        } finally {
            setSettingVisibility(false);
        }
    };
    const [publishing, setPublishing] = useState(false);
    // One discriminated-union state instead of (isEditingName, nameDraft,
    // nameError, isRenaming): they're not independent — error and saving are
    // mutually exclusive, and both are meaningless in the display state. The
    // union makes the bad combinations unrepresentable and structurally
    // prevents the Enter→disable-input→blur→re-fire race.
    type RenameState =
        | { kind: "display" }
        | { kind: "editing"; draft: string; error: string | null }
        | { kind: "saving"; draft: string };
    const [rename, setRename] = useState<RenameState>({ kind: "display" });
    const nameInputRef = useRef<HTMLInputElement>(null);
    const renameButtonRef = useRef<HTMLButtonElement>(null);

    const hasValidationErrors = workflowValidationErrors.length > 0;
    const isCallDisabled = isDirty || hasValidationErrors;

    const handleSave = async () => {
        setSavingWorkflow(true);
        await saveWorkflow();
        setSavingWorkflow(false);
    };

    const handlePublish = async () => {
        if (publishing) return;
        setPublishing(true);
        const promise = publishWorkflowApiV1WorkflowWorkflowIdPublishPost({
            path: { workflow_id: workflowId },
        });
        toast.promise(promise, {
            loading: "Publishing...",
            success: "Workflow published successfully",
            error: "Failed to publish workflow",
        });
        try {
            const { data } = await promise;
            // The acceptable use policy, read against what was just published.
            //
            // A warning and never a refusal: the terms carry a suspension
            // power a person exercises, and a language model reading prose is
            // not the thing to stand between somebody and their own bot. So
            // the bot is live and this names the clause, with the sentence
            // that triggered it, for however long it takes to read.
            const findings =
                (data as { acceptable_use_findings?: AcceptableUseFinding[] } | undefined)
                    ?.acceptable_use_findings ?? [];
            for (const finding of findings) {
                toast.warning(finding.title, {
                    description: `${finding.why} — "${finding.quote}"`,
                    duration: 30_000,
                });
            }
            onPublished();
        } finally {
            setPublishing(false);
        }
    };

    const handleBack = () => {
        router.push("/workflow");
    };

    const handleDuplicate = async () => {
        if (duplicating) return;
        setDuplicating(true);
        const promise = duplicateWorkflowEndpointApiV1WorkflowWorkflowIdDuplicatePost({
            path: { workflow_id: workflowId },
        });
        toast.promise(promise, {
            loading: "Duplicating workflow...",
            success: "Workflow duplicated successfully",
            error: "Failed to duplicate workflow",
        });
        try {
            const { data } = await promise;
            if (data?.id) {
                router.push(`/workflow/${data.id}`);
            }
        } finally {
            setDuplicating(false);
        }
    };

    const handleCopyAgentUuid = async () => {
        if (!workflowUuid) {
            toast.error("Agent UUID not available");
            return;
        }
        try {
            await navigator.clipboard.writeText(workflowUuid);
            toast.success("Agent UUID copied");
        } catch {
            toast.error("Failed to copy Agent UUID");
        }
    };

    const handleDownloadWorkflow = () => {
        if (!rfInstance.current) return;

        const workflowDefinition = rfInstance.current.toObject();
        const exportData = {
            name: workflowName,
            workflow_definition: workflowDefinition,
        };

        const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${workflowName}.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    };

    const enterEditMode = () => {
        setRename({ kind: "editing", draft: workflowName, error: null });
    };

    const exitEditMode = () => {
        setRename({ kind: "display" });
        // Return focus to the pencil button so keyboard users aren't stranded.
        // Defer to next tick so React commits the input unmount first.
        setTimeout(() => renameButtonRef.current?.focus(), 0);
    };

    const attemptSave = async () => {
        // Only "editing" can initiate a save. This also guards against the
        // blur fired when disabling the input transitions us to "saving".
        if (rename.kind !== "editing") return;
        const trimmed = rename.draft.trim();
        if (trimmed.length === 0) {
            setRename({ ...rename, error: "Name cannot be empty" });
            return;
        }
        if (trimmed === workflowName) {
            // No-op: exit cleanly with no API call.
            exitEditMode();
            return;
        }
        setRename({ kind: "saving", draft: rename.draft });
        try {
            await renameWorkflow(trimmed);
            // Success: store update already propagated workflowName. Exit edit mode.
            exitEditMode();
        } catch {
            // Roll back: keep user's typed value, reopen the input, focus it,
            // surface a sonner toast (matches existing duplicate/publish failure pattern).
            toast.error("Failed to rename workflow");
            setRename({ kind: "editing", draft: trimmed, error: null });
            setTimeout(() => nameInputRef.current?.focus(), 0);
        }
    };

    const handleRenameKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
        if (event.key === "Enter") {
            event.preventDefault();
            void attemptSave();
        } else if (event.key === "Escape") {
            event.preventDefault();
            exitEditMode();
        }
    };

    const handleRenameBlur = () => {
        // Ignore the blur fired when the input is disabled during save.
        if (rename.kind !== "editing") return;
        // On blur with empty/whitespace, revert silently to display mode so the user is never trapped.
        if (rename.draft.trim().length === 0) {
            exitEditMode();
            return;
        }
        void attemptSave();
    };

    return (
        <div className="flex min-w-0 w-full flex-col gap-2 border-b border-border bg-card px-3 py-2 lg:flex-row lg:items-center lg:justify-between lg:px-4">
            {/* Left section: Mobile menu + Back button + Workflow name */}
            <div className="flex min-w-0 items-center gap-2 lg:flex-1">
                <button
                    onClick={toggleSidebar}
                    className="flex items-center justify-center w-10 h-10 shrink-0 rounded-lg hover:bg-accent transition-colors md:hidden"
                    aria-label="Open menu"
                >
                    <Menu className="w-5 h-5 text-muted-foreground" />
                </button>
                <button
                    onClick={handleBack}
                    aria-label="Back to agents"
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg hover:bg-accent transition-colors"
                >
                    <ArrowLeft className="w-5 h-5 text-muted-foreground" />
                </button>

                <div className="flex min-w-0 flex-1 items-center gap-2">
                    {rename.kind !== "display" ? (
                        <div className="flex min-w-0 flex-1 flex-col gap-1">
                            <Input
                                ref={nameInputRef}
                                value={rename.draft}
                                onChange={(e) => {
                                    // onChange can't fire while disabled (kind === "saving"),
                                    // but the type guard is needed for the discriminated union.
                                    if (rename.kind === "editing") {
                                        setRename({ ...rename, draft: e.target.value, error: null });
                                    }
                                }}
                                onKeyDown={handleRenameKeyDown}
                                onBlur={handleRenameBlur}
                                disabled={rename.kind === "saving"}
                                autoFocus
                                onFocus={(e) => e.currentTarget.select()}
                                aria-label="Workflow name"
                                aria-invalid={rename.kind === "editing" && rename.error !== null}
                                className="h-8 max-w-xs bg-muted border-input text-foreground text-base font-medium"
                            />
                            {rename.kind === "editing" && rename.error && (
                                <span className="text-xs text-red-500" role="alert">{rename.error}</span>
                            )}
                        </div>
                    ) : (
                        <>
                            <h1 title={workflowName} className="min-w-0 flex-1 truncate text-base font-semibold text-foreground">{workflowName}</h1>
                            {!isViewingHistoricalVersion && (
                                <button
                                    ref={renameButtonRef}
                                    type="button"
                                    onClick={enterEditMode}
                                    aria-label="Rename workflow"
                                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg hover:bg-accent transition-colors"
                                >
                                    <Pencil className="w-4 h-4 text-muted-foreground" />
                                </button>
                            )}
                        </>
                    )}
                </div>
            </div>

            {/* Right section: Version + status + tester/call actions + save */}
            <div className="flex min-w-0 flex-wrap items-center gap-1.5 lg:justify-end">
                {/* Read-only banner when viewing a historical version */}
                {isViewingHistoricalVersion && (
                    <div className="flex min-w-0 max-w-full items-center gap-1.5 rounded-md bg-muted px-2 py-1 text-muted-foreground">
                        <Eye className="w-4 h-4 text-muted-foreground" />
                        <span className="truncate text-xs text-muted-foreground">
                            Viewing {activeVersionLabel} - Read only
                        </span>
                    </div>
                )}

                {/* Back to Draft button when viewing history */}
                {isViewingHistoricalVersion && (
                    <Button
                        onClick={onBackToDraft}
                        className="bg-teal-700 hover:bg-teal-800 text-white px-3"
                    >
                        Back to Draft
                    </Button>
                )}

                {/* Version history button */}
                <button
                    onClick={onHistoryClick}
                    aria-label={`Version history${activeVersionLabel ? `: ${activeVersionLabel}` : ""}`}
                    title={activeVersionLabel ?? "Version history"}
                    className="flex h-9 shrink-0 items-center gap-2 rounded-md border border-input px-2 hover:bg-accent transition-colors cursor-pointer"
                >
                    <History className="w-4 h-4 text-muted-foreground" />
                    {activeVersionLabel && !isViewingHistoricalVersion && (
                        <span className="hidden text-sm text-muted-foreground xl:inline">{activeVersionLabel}</span>
                    )}
                </button>

                {/* Unsaved changes indicator (hidden when viewing history) */}
                {isDirty && !isViewingHistoricalVersion && (
                    <div className="flex items-center gap-1.5 px-1.5 py-1">
                        <div className="w-2 h-2 rounded-full bg-yellow-500" />
                        <span className="text-xs text-muted-foreground" aria-label="Unsaved changes">Unsaved</span>
                    </div>
                )}

                {/* Validation errors indicator */}
                {hasValidationErrors && (
                    <Popover>
                        <PopoverTrigger asChild>
                            <button className="flex h-9 items-center gap-1.5 px-2 py-1 rounded-md border border-red-500/30 bg-red-500/10 hover:bg-red-500/20 transition-colors cursor-pointer">

                                <AlertCircle className="w-4 h-4 text-red-500" />
                                <span className="text-sm text-red-500">
                                    {workflowValidationErrors.length} {workflowValidationErrors.length === 1 ? "error" : "errors"}
                                </span>
                            </button>
                        </PopoverTrigger>
                        <PopoverContent
                            align="end"
                            className="w-[min(20rem,calc(100vw-2rem))] bg-card border-input p-0"
                        >
                            <div className="px-4 py-3 border-b border-input">
                                <h3 className="text-sm font-medium text-foreground">Validation Errors</h3>
                            </div>
                            <div className="max-h-64 overflow-y-auto">
                                {workflowValidationErrors.map((error, index) => (
                                    <div
                                        key={index}
                                        className="px-4 py-3 border-b border-border last:border-b-0"
                                    >
                                        <div className="flex items-start gap-2">
                                            <AlertCircle className="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" />
                                            <div className="flex-1 min-w-0">
                                                {(error.kind === "node" || error.kind === "edge") && error.id && (
                                                    <p className="text-xs text-muted-foreground mb-1">
                                                        {error.kind === "node" ? "Node" : "Edge"}: {error.id}
                                                        {error.field && <span className="text-muted-foreground"> • {error.field}</span>}
                                                    </p>
                                                )}
                                                <p className="text-sm text-foreground break-words">
                                                    {error.message}
                                                </p>
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </PopoverContent>
                    </Popover>
                )}

                {/* Publish button (only when on draft with no unsaved changes) */}
                {!isViewingHistoricalVersion && hasDraft && (
                    <Button
                        onClick={handlePublish}
                        disabled={isDirty || publishing || hasValidationErrors}
                        variant="outline"
                        className="border-input bg-transparent hover:bg-accent text-foreground px-3"
                    >
                        {publishing ? (
                            <>
                                <LoaderCircle className="w-4 h-4 mr-2 animate-spin" />
                                Publishing...
                            </>
                        ) : (
                            <>
                                <Rocket className="w-4 h-4 mr-2" />
                                Publish
                            </>
                        )}
                    </Button>
                )}

                <Button
                    variant="outline"
                    className="flex items-center gap-2 bg-transparent border-input hover:bg-accent text-foreground"
                    onClick={onTestAgentClick}
                >
                    <Bot className="w-4 h-4" />
                    Test
                </Button>

                {/* Save button (only shown when editing the draft) */}
                {!isViewingHistoricalVersion && (
                    <Button
                        onClick={handleSave}
                        disabled={!isDirty || savingWorkflow}
                        className="bg-teal-700 hover:bg-teal-800 text-white px-3"
                    >
                        {savingWorkflow ? (
                            <>
                                <LoaderCircle className="w-4 h-4 mr-2 animate-spin" />
                                Saving...
                            </>
                        ) : (
                            "Save"
                        )}
                    </Button>
                )}

                {/* More options dropdown */}
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button
                            variant="ghost"
                            size="icon"
                            aria-label="More agent actions"
                            className="shrink-0 text-muted-foreground hover:text-foreground hover:bg-accent"
                        >
                            <MoreVertical className="w-5 h-5" />
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="bg-card border-input">
                        {!isViewingHistoricalVersion && <DropdownMenuItem onClick={onPhoneCallClick} disabled={isCallDisabled}>
                            <Phone className="mr-2 h-4 w-4" />Phone Call
                        </DropdownMenuItem>}
                        <DropdownMenuItem
                            onClick={() => router.push(`/workflow/${workflowId}/runs`)}
                            className="text-foreground hover:bg-accent cursor-pointer"
                        >
                            <History className="w-4 h-4 mr-2" />
                            View Runs
                        </DropdownMenuItem>
                        <DropdownMenuItem
                            onClick={handleDuplicate}
                            disabled={duplicating}
                            className="text-foreground hover:bg-accent cursor-pointer"
                        >
                            {duplicating ? (
                                <LoaderCircle className="w-4 h-4 mr-2 animate-spin" />
                            ) : (
                                <Copy className="w-4 h-4 mr-2" />
                            )}
                            {duplicating ? "Duplicating..." : "Duplicate Workflow"}
                        </DropdownMenuItem>
                        {rolesAvailable && !isViewingHistoricalVersion && (
                            <DropdownMenuItem
                                onClick={() => setSavingRole(true)}
                                className="text-foreground hover:bg-accent cursor-pointer"
                            >
                                <BookmarkPlus className="w-4 h-4 mr-2" />
                                Save as workspace role
                            </DropdownMenuItem>
                        )}
                        {rolesAvailable && (
                            <DropdownMenuItem
                                onClick={() => void toggleVisibility()}
                                disabled={settingVisibility}
                                className="text-foreground hover:bg-accent cursor-pointer"
                            >
                                <Eye className="w-4 h-4 mr-2" />
                                {visibility === "admins"
                                    ? "Visible to admins only — show to everyone"
                                    : "Visible to everyone — hide from members"}
                            </DropdownMenuItem>
                        )}
                        <DropdownMenuItem
                            onClick={handleDownloadWorkflow}
                            className="text-foreground hover:bg-accent cursor-pointer"
                        >
                            <Download className="w-4 h-4 mr-2" />
                            Download Workflow
                        </DropdownMenuItem>
                        <DropdownMenuItem
                            onClick={handleCopyAgentUuid}
                            disabled={!workflowUuid}
                            className="text-foreground hover:bg-accent cursor-pointer"
                        >
                            <Clipboard className="w-4 h-4 mr-2" />
                            Copy Agent UUID
                        </DropdownMenuItem>
                    </DropdownMenuContent>
                </DropdownMenu>
                {rolesAvailable && (
                    <SaveAsRoleDialog
                        workflowId={workflowId}
                        agentName={workflowName}
                        open={savingRole}
                        onOpenChange={setSavingRole}
                    />
                )}
            </div>
        </div>
    );
};
