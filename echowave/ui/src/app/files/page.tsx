"use client";

import { ExternalLink, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    ensureFileFolderPathApiV1KnowledgeBaseFileFoldersEnsurePost,
    listFileFoldersApiV1KnowledgeBaseFileFoldersGet,
    updateDocumentApiV1KnowledgeBaseDocumentsDocumentUuidPatch,
} from "@/client/sdk.gen";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { KNOWLEDGE_TABS } from "@/components/layout/SectionTabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { rejectFile, uploadKnowledge } from "@/lib/uploadKnowledge";

import DocumentList from "./DocumentList";
import DocumentUpload from "./DocumentUpload";
import { carriesDesktopFiles, type DroppedFile, type FileFolder, readDrop } from "./fileFolders";
import { type DropTarget, FolderBrowser } from "./FolderBrowser";

export default function FilesPage() {
    const { user, redirectToLogin, loading } = useAuth();
    const [refreshKey, setRefreshKey] = useState(0);
    const [isUploadOpen, setIsUploadOpen] = useState(false);
    const [dragging, setDragging] = useState(false);
    const [dropping, setDropping] = useState<string | null>(null);
    const [folders, setFolders] = useState<FileFolder[]>([]);
    const [currentId, setCurrentId] = useState<number | null>(null);
    // dragenter/dragleave fire for every child crossed; a count of the ones
    // still under the pointer is what says the drag left the page.
    const depth = useRef(0);

    const loadFolders = useCallback(async () => {
        const response = await listFileFoldersApiV1KnowledgeBaseFileFoldersGet();
        if (response.error) {
            toast.error(detailFromError(response.error, "Could not load your folders"));
            return;
        }
        const next = response.data?.folders ?? [];
        setFolders(next);
        // The folder being shown was deleted elsewhere: go back to the top
        // rather than showing an empty page for a folder that is gone.
        setCurrentId((was) => (was != null && !next.some((f) => f.id === was) ? null : was));
    }, []);

    useEffect(() => {
        if (loading || !user) return;
        void loadFolders();
    }, [loading, user, loadFolders]);

    const refreshAll = useCallback(() => {
        setRefreshKey((prev) => prev + 1);
        void loadFolders();
    }, [loadFolders]);

    /**
     * Every dropped file, one after another, into `target` -- or, for a
     * folder dragged from the desktop, into the same structure under it
     * (made as needed, reused when it is already there). Read by every
     * agent, through the same door the Upload dialog uses. A file that
     * cannot be read is named and skipped, never silently lost.
     */
    const addDropped = async (dropped: DroppedFile[], target: DropTarget) => {
        let added = 0;
        const skipped: { label: string; why: string }[] = [];
        const made = new Map<string, number | null>();

        const folderFor = async (path: string[]): Promise<number | null> => {
            if (path.length === 0) return target;
            const key = path.join("/");
            if (made.has(key)) return made.get(key)!;
            const response = await ensureFileFolderPathApiV1KnowledgeBaseFileFoldersEnsurePost({
                body: { parent_id: target, path },
            });
            if (response.error) throw new Error(detailFromError(response.error, `Could not make the folder ${key}`));
            const id = response.data?.id ?? target;
            made.set(key, id);
            return id;
        };

        for (const { file, path } of dropped) {
            const label = [...path, file.name].join("/");
            const why = rejectFile(file);
            if (why) {
                skipped.push({ label, why });
                continue;
            }
            setDropping(label);
            try {
                const fileFolderId = await folderFor(path);
                await uploadKnowledge(file, { scope: "org" }, { fileFolderId });
                added += 1;
            } catch (error) {
                toast.error(`${label}: ${error instanceof Error ? error.message : "it was not added"}`);
            }
        }
        setDropping(null);
        if (skipped.length === 1) {
            toast.error(`${skipped[0].label}: ${skipped[0].why}`);
        } else if (skipped.length > 1) {
            const shown = skipped.slice(0, 3).map((s) => s.label).join(", ");
            const more = skipped.length > 3 ? ` and ${skipped.length - 3} more` : "";
            toast.error(`${skipped.length} files were not added: ${shown}${more}. ${skipped[0].why}`);
        }
        if (added) {
            toast.success(added === 1 ? "1 file added. Reading it now." : `${added} files added. Reading them now.`);
        }
        if (added || made.size) refreshAll();
    };

    const dropInto = (target: DropTarget, data: DataTransfer) => {
        // Read synchronously, inside the event: the browser empties the
        // DataTransfer once the handler returns.
        const reading = readDrop(data);
        void reading.then((dropped) => addDropped(dropped, target));
    };

    const moveFile = async (documentUuid: string, target: DropTarget) => {
        const response = await updateDocumentApiV1KnowledgeBaseDocumentsDocumentUuidPatch({
            path: { document_uuid: documentUuid },
            body: { file_folder_id: target },
        });
        if (response.error) {
            toast.error(detailFromError(response.error, "Could not move the file"));
            return;
        }
        const where = target == null ? "All files" : folders.find((f) => f.id === target)?.path ?? "the folder";
        toast.success(`Moved "${response.data?.filename ?? "the file"}" to ${where}`);
        refreshAll();
    };

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    const handleUploadSuccess = () => {
        refreshAll();
        setIsUploadOpen(false);
    };

    if (loading || !user) {
        return (
            <PageBody className="space-y-4">
                <Skeleton className="h-12 w-full max-w-64" />
                <Skeleton className="h-64 w-full" />
            </PageBody>
        );
    }

    const currentName = currentId == null ? null : folders.find((f) => f.id === currentId)?.name ?? null;

    return (
        <div
            className="relative min-h-full"
            data-testid="files-drop-zone"
            onDragEnter={(event) => {
                if (!carriesDesktopFiles(event.dataTransfer) || isUploadOpen) return;
                event.preventDefault();
                depth.current += 1;
                setDragging(true);
            }}
            onDragOver={(event) => {
                if (!carriesDesktopFiles(event.dataTransfer) || isUploadOpen) return;
                event.preventDefault();
            }}
            onDragLeave={(event) => {
                if (!carriesDesktopFiles(event.dataTransfer) || isUploadOpen) return;
                depth.current = Math.max(0, depth.current - 1);
                if (depth.current === 0) setDragging(false);
            }}
            onDrop={(event) => {
                depth.current = 0;
                setDragging(false);
                if (!carriesDesktopFiles(event.dataTransfer) || isUploadOpen) return;
                event.preventDefault();
                dropInto(currentId, event.dataTransfer);
            }}
        >
            {dragging && (
                <div
                    aria-hidden
                    className="pointer-events-none absolute inset-2 z-40 flex items-center justify-center rounded-2xl border-2 border-dashed border-foreground/30 bg-background/85 text-lg font-medium"
                >
                    {currentName ? `Drop to add to ${currentName}` : "Drop to add to Files"}
                </div>
            )}
            <PageHeader
                tabs={KNOWLEDGE_TABS}
                title={
                    <span className="flex flex-wrap items-center gap-2">
                        Files
                        {/* Retrieval during a call is a real, measured cost, but
                            it is not billed as a separate line today — see
                            PRICING-DECISIONS.md. An absorbed feature nobody is
                            told about earns nothing, so this says so where an
                            account actually decides whether to use it. */}
                        <Badge
                            variant="secondary"
                            className="bg-emerald-500/12 text-emerald-700 dark:text-emerald-300"
                        >
                            Included — no extra charge
                        </Badge>
                    </span>
                }
                description={
                    <>
                        What every agent here reads: your price list, policies, FAQs. Drag files or whole folders anywhere on this page to add them. Drop a file into a channel or an agent&apos;s chat to give it to just them.{" "}
                        <a href="https://docs.decibyl.ai/voice-agent/knowledge-base" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                            Learn more <ExternalLink className="h-3 w-3" />
                        </a>
                    </>
                }
                actions={
                    <Button onClick={() => setIsUploadOpen(true)}>
                        <Upload className="mr-2 h-4 w-4" />
                        Upload
                    </Button>
                }
            />
            <PageBody>
            <Card>
                <CardHeader>
                    <CardTitle>Your files</CardTitle>
                    <CardDescription>
                        {dropping ? (
                            <span role="status">Adding {dropping}…</span>
                        ) : null}{" "}
                        Files here are read by every agent, whichever folder they are in. A file given to a channel or an agent is read there only. Library files are read by the steps that name them.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <FolderBrowser
                        folders={folders}
                        currentId={currentId}
                        onOpen={setCurrentId}
                        onChanged={refreshAll}
                        onDropFiles={dropInto}
                        onMoveFile={(uuid, target) => void moveFile(uuid, target)}
                    />
                    <DocumentList
                        refreshTrigger={refreshKey}
                        fileFolderId={currentId}
                        folders={folders}
                        onChanged={() => void loadFolders()}
                    />
                </CardContent>
            </Card>

            <Dialog open={isUploadOpen} onOpenChange={setIsUploadOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>{currentName ? `Add to ${currentName}` : "Add to Files"}</DialogTitle>
                        <DialogDescription>
                            Every agent in this workspace will be able to answer from it.
                        </DialogDescription>
                    </DialogHeader>
                    <DocumentUpload onUploadSuccess={handleUploadSuccess} target={{ scope: 'org' }} fileFolderId={currentId} />
                </DialogContent>
            </Dialog>
            </PageBody>
        </div>
    );
}
