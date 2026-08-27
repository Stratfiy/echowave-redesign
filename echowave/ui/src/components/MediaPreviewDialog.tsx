'use client';

import { AlertTriangle,Headphones, Loader2 } from 'lucide-react';
import posthog from 'posthog-js';
import { useCallback, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogClose,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { PostHogEvent } from '@/constants/posthog-events';
import { downloadFile, getSignedUrl } from '@/lib/files';

export function MediaPreviewDialog() {
    const [isOpen, setIsOpen] = useState(false);
    const [audioSignedUrl, setAudioSignedUrl] = useState<string | null>(null);
    const [transcriptContent, setTranscriptContent] = useState<string | null>(null);
    const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
    const [recordingKey, setRecordingKey] = useState<string | null>(null);
    const [transcriptKey, setTranscriptKey] = useState<string | null>(null);
    const [mediaLoading, setMediaLoading] = useState(false);
    /**
     * Why nothing loaded, when something was supposed to.
     *
     * This dialog only opens for a run that *has* a recording or transcript key
     * — `MediaPreviewButton` renders nothing otherwise, and `openPreview`
     * returns early. So "No recording or transcript available" was never the
     * right sentence for an empty dialog: the file is on the run, and what
     * failed was fetching it. It reported a missing recording for a 403 on
     * somebody else's run, for a key the storage backend had never heard of,
     * and for MinIO being down, which are three different problems and one of
     * them is not the customer's.
     */
    const [loadError, setLoadError] = useState<string | null>(null);

    const openPreview = useCallback(
        async (recordingUrl: string | null, transcriptUrl: string | null, runId: number) => {
            if (!recordingUrl && !transcriptUrl) return;
            setMediaLoading(true);
            setAudioSignedUrl(null);
            setTranscriptContent(null);
            setLoadError(null);
            setRecordingKey(recordingUrl);
            setTranscriptKey(transcriptUrl);
            setSelectedRunId(runId);
            setIsOpen(true);

            const [audioResult, transcriptResult] = await Promise.all([
                recordingUrl ? getSignedUrl(recordingUrl) : null,
                transcriptUrl ? getSignedUrl(transcriptUrl, true) : null,
            ]);

            const failures: string[] = [];

            if (audioResult?.url) {
                setAudioSignedUrl(audioResult.url);
            } else if (audioResult?.error) {
                failures.push(`Recording: ${audioResult.error}`);
            }

            if (transcriptResult?.url) {
                try {
                    const response = await fetch(transcriptResult.url);
                    if (!response.ok) {
                        // The signed URL was issued and storage still refused
                        // it. Usually an object the key points at that is not
                        // there — worth distinguishing from being denied the
                        // URL in the first place.
                        throw new Error(`storage returned ${response.status}`);
                    }
                    const text = await response.text();
                    setTranscriptContent(text);
                    posthog.capture(PostHogEvent.TRANSCRIPT_VIEWED, {
                        run_id: runId,
                        source: 'media_preview_dialog',
                        transcript_length: text.length,
                    });
                } catch (error) {
                    console.error('Error fetching transcript:', error);
                    failures.push(
                        `Transcript: could not be downloaded (${
                            error instanceof Error ? error.message : 'unknown error'
                        }).`,
                    );
                }
            } else if (transcriptResult?.error) {
                failures.push(`Transcript: ${transcriptResult.error}`);
            }

            setLoadError(failures.length > 0 ? failures.join(' ') : null);
            setMediaLoading(false);
        },
        [],
    );

    return {
        openPreview,
        dialog: (
            <Dialog open={isOpen} onOpenChange={setIsOpen}>
                <DialogContent className="sm:max-w-2xl">
                    <DialogHeader>
                        <DialogTitle>
                            Run Preview
                            {selectedRunId && ` - Run #${selectedRunId}`}
                        </DialogTitle>
                    </DialogHeader>

                    {mediaLoading && (
                        <div className="flex items-center justify-center py-8 space-x-2">
                            <Loader2 className="h-6 w-6 animate-spin" />
                            <span>Loading...</span>
                        </div>
                    )}

                    {!mediaLoading && audioSignedUrl && (
                        <audio
                            src={audioSignedUrl}
                            controls
                            autoPlay
                            className="w-full mt-4"
                            onPlay={() => posthog.capture(PostHogEvent.RECORDING_PLAYED, {
                                run_id: selectedRunId,
                                source: 'media_preview_dialog',
                            })}
                        />
                    )}

                    {!mediaLoading && transcriptContent && (
                        <pre className="w-full h-[60vh] overflow-auto border rounded-md mt-4 p-4 bg-muted text-sm whitespace-pre-wrap font-mono">
                            {transcriptContent}
                        </pre>
                    )}

                    {/* The failure, when there was one. Named rather than
                        summarised as absence: this run has the file, and
                        whoever is reading needs to know whether to retry, ask
                        for access, or tell us storage is down. */}
                    {!mediaLoading && loadError && (
                        <div className="mt-4 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                            <span>{loadError}</span>
                        </div>
                    )}

                    {!mediaLoading && !loadError && !audioSignedUrl && !transcriptContent && (
                        <div className="flex items-center justify-center py-8 text-muted-foreground">
                            No recording or transcript available.
                        </div>
                    )}

                    <DialogFooter className="pt-4">
                        <DialogClose asChild>
                            <Button variant="secondary">Close</Button>
                        </DialogClose>
                        <div className="flex gap-2">
                            {/* A download that fails now says so. It used to
                                open nothing and log to a console nobody has
                                open, which reads as a dead button. */}
                            {recordingKey && (
                                <Button
                                    variant="outline"
                                    onClick={async () =>
                                        setLoadError(await downloadFile(recordingKey))
                                    }
                                >
                                    Download Recording
                                </Button>
                            )}
                            {transcriptKey && (
                                <Button
                                    variant="outline"
                                    onClick={async () =>
                                        setLoadError(await downloadFile(transcriptKey))
                                    }
                                >
                                    Download Transcript
                                </Button>
                            )}
                        </div>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        ),
    };
}

interface MediaPreviewButtonProps {
    recordingUrl: string | null | undefined;
    transcriptUrl: string | null | undefined;
    runId: number;
    onOpenPreview: (recordingUrl: string | null, transcriptUrl: string | null, runId: number) => void;
    onSelect?: (runId: number) => void;
}

export function MediaPreviewButton({
    recordingUrl,
    transcriptUrl,
    runId,
    onOpenPreview,
    onSelect,
}: MediaPreviewButtonProps) {
    if (!recordingUrl && !transcriptUrl) return null;

    const handleOpen = () => {
        onSelect?.(runId);
        onOpenPreview(recordingUrl ?? null, transcriptUrl ?? null, runId);
    };

    return (
        <Button
            variant="outline"
            size="icon"
            onClick={handleOpen}
        >
            <Headphones className="h-4 w-4" />
        </Button>
    );
}
