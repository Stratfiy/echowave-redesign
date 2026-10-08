"use client";

import { ExternalLink, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

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
import { useAuth } from "@/lib/auth";
import { rejectFile, uploadKnowledge } from "@/lib/uploadKnowledge";

import DocumentList from "./DocumentList";
import DocumentUpload from "./DocumentUpload";

export default function FilesPage() {
    const { user, redirectToLogin, loading } = useAuth();
    const [refreshKey, setRefreshKey] = useState(0);
    const [isUploadOpen, setIsUploadOpen] = useState(false);
    const [dragging, setDragging] = useState(false);
    const [dropping, setDropping] = useState<string | null>(null);
    // dragenter/dragleave fire for every child crossed; a count of the ones
    // still under the pointer is what says the drag left the page.
    const depth = useRef(0);

    /** A drop anywhere on the page adds every file in it, one after another,
     *  read by every agent -- the same door the Upload dialog uses. */
    const addDropped = async (files: File[]) => {
        let added = 0;
        for (const file of files) {
            const why = rejectFile(file);
            if (why) {
                toast.error(`${file.name}: ${why}`);
                continue;
            }
            setDropping(file.name);
            try {
                await uploadKnowledge(file, { scope: "org" });
                added += 1;
            } catch (error) {
                toast.error(`${file.name}: ${error instanceof Error ? error.message : "it was not added"}`);
            }
        }
        setDropping(null);
        if (added) {
            toast.success(added === 1 ? "1 file added. Reading it now." : `${added} files added. Reading them now.`);
            setRefreshKey((prev) => prev + 1);
        }
    };

    const hasFiles = (event: React.DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes("Files");

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    const handleUploadSuccess = () => {
        setRefreshKey(prev => prev + 1);
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

    return (
        <div
            className="relative min-h-full"
            data-testid="files-drop-zone"
            onDragEnter={(event) => {
                if (!hasFiles(event) || isUploadOpen) return;
                event.preventDefault();
                depth.current += 1;
                setDragging(true);
            }}
            onDragOver={(event) => {
                if (!hasFiles(event) || isUploadOpen) return;
                event.preventDefault();
            }}
            onDragLeave={(event) => {
                if (!hasFiles(event) || isUploadOpen) return;
                depth.current = Math.max(0, depth.current - 1);
                if (depth.current === 0) setDragging(false);
            }}
            onDrop={(event) => {
                if (!hasFiles(event) || isUploadOpen) return;
                event.preventDefault();
                depth.current = 0;
                setDragging(false);
                void addDropped(Array.from(event.dataTransfer.files ?? []));
            }}
        >
            {dragging && (
                <div
                    aria-hidden
                    className="pointer-events-none absolute inset-2 z-40 flex items-center justify-center rounded-2xl border-2 border-dashed border-foreground/30 bg-background/85 text-lg font-medium"
                >
                    Drop to add to Files
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
                        What every agent here reads: your price list, policies, FAQs. Drag files anywhere on this page to add them. Drop a file into a channel or an agent&apos;s chat to give it to just them.{" "}
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
                        Files here are read by every agent. A file given to a channel or an agent is read there only. Library files are read by the steps that name them.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <DocumentList refreshTrigger={refreshKey} />
                </CardContent>
            </Card>

            <Dialog open={isUploadOpen} onOpenChange={setIsUploadOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Add to Files</DialogTitle>
                        <DialogDescription>
                            Every agent in this workspace will be able to answer from it.
                        </DialogDescription>
                    </DialogHeader>
                    <DocumentUpload onUploadSuccess={handleUploadSuccess} target={{ scope: 'org' }} />
                </DialogContent>
            </Dialog>
            </PageBody>
        </div>
    );
}
