"use client";

import { formatDistanceToNow } from "date-fns";
import { FileText, History, LoaderCircle, X } from "lucide-react";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";

export interface WorkflowVersion {
    id: number;
    version_number: number;
    status: string;
    created_at: string;
    published_at: string | null;
    workflow_json: { nodes?: unknown[]; edges?: unknown[]; viewport?: unknown };
    workflow_configurations: Record<string, unknown> | null;
    template_context_variables: Record<string, string> | null;
}

interface VersionHistoryPanelProps {
    isOpen: boolean;
    onClose: () => void;
    versions: WorkflowVersion[];
    loading: boolean;
    activeVersionId: number | null;
    onSelectVersion: (version: WorkflowVersion) => void;
    /** Copy this version into the draft. The undo for a bad publish: nothing
     *  goes live until Publish is pressed on the draft it makes. */
    onRestoreVersion?: (version: WorkflowVersion) => void;
    hasMore: boolean;
    loadingMore: boolean;
    onLoadMore: () => void;
}

const statusLabel: Record<string, string> = {
    draft: "Draft",
    published: "Published",
    archived: "Archived",
};

const statusColor: Record<string, string> = {
    draft: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
    published: "bg-green-500/20 text-green-400 border-green-500/30",
    archived: "bg-gray-500/20 text-muted-foreground border-gray-500/30",
};

export const VersionHistoryPanel = ({
    isOpen,
    onClose,
    versions,
    loading,
    activeVersionId,
    onSelectVersion,
    onRestoreVersion,
    hasMore,
    loadingMore,
    onLoadMore,
}: VersionHistoryPanelProps) => {
    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape" && isOpen) {
                onClose();
            }
        };
        document.addEventListener("keydown", handleKeyDown);
        return () => document.removeEventListener("keydown", handleKeyDown);
    }, [isOpen, onClose]);

    return (
        <div
            className={`fixed z-51 right-0 top-0 h-full w-80 bg-card border-l border-border shadow-lg transform transition-transform duration-300 ease-in-out ${
                isOpen ? "translate-x-0" : "translate-x-full"
            }`}
        >
            <div className="p-4 h-full overflow-y-auto">
                <div className="flex justify-between items-center mb-6">
                    <h2 className="text-lg font-semibold text-foreground">
                        Version History
                    </h2>
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={onClose}
                        className="text-muted-foreground hover:text-foreground hover:bg-accent"
                    >
                        <X className="w-5 h-5" />
                    </Button>
                </div>

                {loading ? (
                    <div className="flex items-center justify-center py-12">
                        <LoaderCircle className="w-6 h-6 text-muted-foreground animate-spin" />
                    </div>
                ) : versions.length === 0 ? (
                    <p className="text-sm text-muted-foreground text-center py-8">
                        No versions found.
                    </p>
                ) : (
                    <div className="space-y-2">
                        {versions.map((version) => {
                            const isActive = version.id === activeVersionId;
                            const date = version.published_at || version.created_at;
                            // The draft is what restoring writes into, so it
                            // cannot be restored; anything else can, the
                            // live version included, which is how a draft
                            // gone wrong is thrown back to what is live.
                            const restorable = !!onRestoreVersion && version.status !== "draft";
                            return (
                                <div
                                    key={version.id}
                                    className={`w-full text-left p-3 rounded-lg border transition-colors ${
                                        isActive
                                            ? "border-teal-500/50 bg-teal-500/10"
                                            : "border-border bg-[#222] hover:bg-accent"
                                    }`}
                                >
                                <button
                                    type="button"
                                    onClick={() => onSelectVersion(version)}
                                    className="w-full text-left cursor-pointer"
                                >
                                    <div className="flex items-center justify-between mb-1.5">
                                        <div className="flex items-center gap-2">
                                            <FileText className="w-4 h-4 text-muted-foreground" />
                                            <span className="text-sm font-medium text-foreground">
                                                v{version.version_number}
                                            </span>
                                        </div>
                                        {version.status !== "archived" && (
                                            <span
                                                className={`text-xs px-2 py-0.5 rounded-full border ${
                                                    statusColor[version.status] ?? ""
                                                }`}
                                            >
                                                {statusLabel[version.status] ?? version.status}
                                            </span>
                                        )}
                                    </div>
                                    <p className="text-xs text-muted-foreground">
                                        {formatDistanceToNow(new Date(date), {
                                            addSuffix: true,
                                        })}
                                    </p>
                                </button>
                                {restorable && (
                                    <button
                                        type="button"
                                        onClick={() => onRestoreVersion?.(version)}
                                        aria-label={`Restore v${version.version_number}`}
                                        className="mt-2 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
                                    >
                                        <History className="w-3 h-3" aria-hidden />
                                        Restore as draft
                                    </button>
                                )}
                                </div>
                            );
                        })}
                        {hasMore && (
                            <Button
                                variant="ghost"
                                onClick={onLoadMore}
                                disabled={loadingMore}
                                className="w-full text-sm text-muted-foreground hover:text-foreground hover:bg-accent"
                            >
                                {loadingMore ? (
                                    <LoaderCircle className="w-4 h-4 animate-spin" />
                                ) : (
                                    "Load more"
                                )}
                            </Button>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};
