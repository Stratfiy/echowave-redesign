"use client";

import { ExternalLink, Upload } from "lucide-react";
import { useEffect, useState } from "react";

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

import DocumentList from "./DocumentList";
import DocumentUpload from "./DocumentUpload";

export default function FilesPage() {
    const { user, redirectToLogin, loading } = useAuth();
    const [refreshKey, setRefreshKey] = useState(0);
    const [isUploadOpen, setIsUploadOpen] = useState(false);

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
        <>
            <PageHeader
                tabs={KNOWLEDGE_TABS}
                title={
                    <span className="flex flex-wrap items-center gap-2">
                        Company knowledge
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
                        What every bot here reads: your price list, policies, FAQs. Drop a file into a channel or a bot&apos;s chat to give it to just them.{" "}
                        <a href="https://docs.decibyl.ai/voice-agent/knowledge-base" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                            Learn more <ExternalLink className="h-3 w-3" />
                        </a>
                    </>
                }
                actions={
                    <Button onClick={() => setIsUploadOpen(true)}>
                        <Upload className="mr-2 h-4 w-4" />
                        Upload document
                    </Button>
                }
            />
            <PageBody>
            <Card>
                <CardHeader>
                    <CardTitle>Documents</CardTitle>
                    <CardDescription>
                        Company knowledge is read by every bot. A file given to a channel or a bot is read there only. Library files are read by the steps that name them.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <DocumentList refreshTrigger={refreshKey} />
                </CardContent>
            </Card>

            <Dialog open={isUploadOpen} onOpenChange={setIsUploadOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Add company knowledge</DialogTitle>
                        <DialogDescription>
                            Every bot in this workspace will be able to answer from it.
                        </DialogDescription>
                    </DialogHeader>
                    <DocumentUpload onUploadSuccess={handleUploadSuccess} target={{ scope: 'org' }} />
                </DialogContent>
            </Dialog>
            </PageBody>
        </>
    );
}
