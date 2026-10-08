"use client";

/**
 * Screen 11: meeting capture.
 *
 * Three ways in -- Record this meeting, Upload a recording, Paste notes --
 * each showing whether it is available here or needs setup, before anything
 * is captured. Audio needs the person to confirm they have the participants'
 * permission, and the source is stated in exactly what it is: this device's
 * microphone, never "any audio". Record mode puts source, language and
 * consent above a large timer that follows real capture, then the live
 * transcript, with pauses and gaps where they happened.
 */

import { AlertTriangle, ArrowLeft, FileAudio, Loader2, Mic, NotebookPen, Pause, Play, Square } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
    addSegmentApiV1MeetingsMeetingIdSegmentsPost,
    createMeetingApiV1MeetingsPost,
    getMeetingApiV1MeetingsMeetingIdGet,
    meetingCapabilitiesApiV1MeetingsCapabilitiesGet,
    pauseMeetingApiV1MeetingsMeetingIdPausePost,
    reportGapApiV1MeetingsMeetingIdGapsPost,
    resumeMeetingApiV1MeetingsMeetingIdResumePost,
    stopMeetingApiV1MeetingsMeetingIdStopPost,
    uploadRecordingApiV1MeetingsMeetingIdUploadPost,
} from "@/client/sdk.gen";
import type { MeetingCapabilities, MeetingRecord } from "@/client/types.gen";
import { ErrorState } from "@/components/shell/ErrorState";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { clock } from "@/lib/meetings/format";
import { type Gap, type RecorderState, type Segment, SegmentedRecorder } from "@/lib/meetings/recorder";
import { UploadQueue } from "@/lib/meetings/uploadQueue";
import { canDictate } from "@/lib/useDictation";
import { cn } from "@/lib/utils";

import { LevelMeter, useInputLevel } from "./LevelMeter";
import { TranscriptView } from "./TranscriptView";

type Source = "microphone" | "upload" | "notes";
type Phase = "choose" | "setup" | "capturing" | "finishing";

const CHOICES: { source: Source; title: string; detail: string; Icon: typeof Mic }[] = [
    { source: "microphone", title: "Record this meeting", detail: "With this device's microphone", Icon: Mic },
    { source: "upload", title: "Upload a recording", detail: "A file you already have", Icon: FileAudio },
    { source: "notes", title: "Paste notes", detail: "No recording needed", Icon: NotebookPen },
];

const SOURCE_LINE: Record<Source, string> = {
    microphone: "This device's microphone",
    upload: "An uploaded recording",
    notes: "Pasted notes",
};

export function parseParticipants(value: string): string[] {
    return value
        .split(/[,\n]/)
        .map((name) => name.trim())
        .filter(Boolean)
        .slice(0, 30);
}

export function MeetingCapture({ threadId }: { threadId: string | null }) {
    const router = useRouter();
    const { user, loading: authLoading } = useAuth();
    const [caps, setCaps] = useState<MeetingCapabilities | null>(null);
    const [capsError, setCapsError] = useState<string | null>(null);
    const [phase, setPhase] = useState<Phase>("choose");
    const [source, setSource] = useState<Source | null>(null);
    const [title, setTitle] = useState("");
    const [language, setLanguage] = useState("unknown");
    const [people, setPeople] = useState("");
    const [consent, setConsent] = useState(false);
    const [notes, setNotes] = useState("");
    const [file, setFile] = useState<File | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const filePicker = useRef<HTMLInputElement | null>(null);

    // Live capture.
    const [meeting, setMeeting] = useState<MeetingRecord | null>(null);
    const [stream, setStream] = useState<MediaStream | null>(null);
    const [recState, setRecState] = useState<RecorderState>("idle");
    const [elapsed, setElapsed] = useState(0);
    const [waiting, setWaiting] = useState(0);
    const [lost, setLost] = useState(0);
    const [online, setOnline] = useState(true);
    const recorder = useRef<SegmentedRecorder | null>(null);
    const queue = useRef<UploadQueue<Segment> | null>(null);

    const loadCaps = useCallback(async () => {
        setCapsError(null);
        const response = await meetingCapabilitiesApiV1MeetingsCapabilitiesGet();
        if (response.error) {
            setCapsError(detailFromResult(response, "Could not check what is set up"));
            return;
        }
        setCaps(response.data as MeetingCapabilities);
    }, []);

    const fetched = useRef(false);
    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void loadCaps();
    }, [authLoading, user, loadCaps]);

    // The timer follows real capture.
    useEffect(() => {
        if (phase !== "capturing") return;
        const timer = window.setInterval(() => setElapsed(recorder.current?.clock.elapsed() ?? 0), 250);
        return () => window.clearInterval(timer);
    }, [phase]);

    // Leaving mid-meeting loses what has not been sent: ask first.
    useEffect(() => {
        if (phase !== "capturing" && phase !== "finishing") return;
        const warn = (event: BeforeUnloadEvent) => {
            event.preventDefault();
        };
        window.addEventListener("beforeunload", warn);
        return () => window.removeEventListener("beforeunload", warn);
    }, [phase]);

    useEffect(() => {
        const update = () => setOnline(typeof navigator === "undefined" || navigator.onLine !== false);
        update();
        window.addEventListener("online", update);
        window.addEventListener("offline", update);
        return () => {
            window.removeEventListener("online", update);
            window.removeEventListener("offline", update);
        };
    }, []);

    // The live transcript, while recording.
    useEffect(() => {
        if (phase !== "capturing" || !meeting) return;
        const timer = window.setInterval(async () => {
            if (document.visibilityState === "hidden") return;
            const response = await getMeetingApiV1MeetingsMeetingIdGet({ path: { meeting_id: meeting.id } });
            if (!response.error && response.data) setMeeting(response.data as MeetingRecord);
        }, 3000);
        return () => window.clearInterval(timer);
    }, [phase, meeting?.id]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(
        () => () => {
            stream?.getTracks().forEach((track) => track.stop());
        },
        [stream],
    );

    const offer = (which: Source) => caps?.sources?.[which];
    const choose = (which: Source) => {
        setSource(which);
        setProblem(null);
        setPhase("setup");
    };

    const create = async (which: Source): Promise<MeetingRecord | null> => {
        const response = await createMeetingApiV1MeetingsPost({
            body: {
                source: which,
                title: title.trim() || null,
                language,
                participants: parseParticipants(people),
                consent_confirmed: which !== "notes" && consent,
                origin_thread_id: threadId,
                notes: which === "notes" ? notes : null,
            },
        });
        if (response.error) {
            setProblem(detailFromResult(response, "Could not start the meeting"));
            return null;
        }
        return response.data as MeetingRecord;
    };

    const startRecording = async () => {
        if (!canDictate()) {
            setProblem("This browser cannot record audio. Upload a recording or paste notes instead.");
            return;
        }
        setBusy(true);
        setProblem(null);
        let media: MediaStream;
        try {
            media = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch {
            setBusy(false);
            setProblem(
                "Microphone access was refused. Allow it in your browser's site settings, or upload a recording or paste notes instead.",
            );
            return;
        }
        const made = await create("microphone");
        if (!made) {
            media.getTracks().forEach((track) => track.stop());
            setBusy(false);
            return;
        }
        setMeeting(made);
        setStream(media);
        const id = made.id;
        queue.current = new UploadQueue<Segment>({
            send: async (segment) => {
                const response = await addSegmentApiV1MeetingsMeetingIdSegmentsPost({
                    path: { meeting_id: id },
                    body: {
                        file: new File([segment.blob], `part-${segment.seq}.webm`, { type: segment.mimeType }),
                        seq: segment.seq,
                        start_ms: segment.startMs,
                        end_ms: segment.endMs,
                    },
                });
                if (!response.error) {
                    if (response.data) setMeeting(response.data as MeetingRecord);
                    return "ok";
                }
                const status = response.response?.status ?? 0;
                return status >= 400 && status < 500 && status !== 408 && status !== 429 ? "refused" : "retry";
            },
            onChange: setWaiting,
            onGiveUp: () => setLost((n) => n + 1),
        });
        recorder.current = new SegmentedRecorder({
            stream: media,
            segmentMs: (caps?.segment_seconds ?? 25) * 1000,
            onSegment: (segment) => queue.current?.enqueue(String(segment.seq), segment),
            onGap: (gap: Gap) => {
                void reportGapApiV1MeetingsMeetingIdGapsPost({
                    path: { meeting_id: id },
                    body: { at_ms: gap.atMs, duration_ms: gap.durationMs, reason: gap.reason },
                });
            },
            onState: setRecState,
        });
        recorder.current.start();
        setBusy(false);
        setPhase("capturing");
    };

    const pause = async () => {
        if (!meeting) return;
        recorder.current?.pause();
        await pauseMeetingApiV1MeetingsMeetingIdPausePost({ path: { meeting_id: meeting.id } });
    };
    const pausedAt = useRef<number>(0);
    useEffect(() => {
        if (recState === "paused") pausedAt.current = Date.now();
    }, [recState]);
    const resume = async () => {
        if (!meeting) return;
        const pausedMs = pausedAt.current ? Date.now() - pausedAt.current : null;
        recorder.current?.resume();
        await resumeMeetingApiV1MeetingsMeetingIdResumePost({
            path: { meeting_id: meeting.id },
            body: { at_ms: recorder.current?.clock.elapsed() ?? 0, paused_ms: pausedMs },
        });
    };
    const reconnect = async () => {
        try {
            const media = await navigator.mediaDevices.getUserMedia({ audio: true });
            stream?.getTracks().forEach((track) => track.stop());
            setStream(media);
            recorder.current?.replaceStream(media);
        } catch {
            setProblem("The microphone is still not available. Stop to save what was captured.");
        }
    };

    const stop = async () => {
        if (!meeting || !recorder.current) return;
        setPhase("finishing");
        const { lastSeq, capturedMs } = await recorder.current.stop();
        stream?.getTracks().forEach((track) => track.stop());
        // Send what is waiting; a part that cannot be sent becomes a gap.
        await queue.current?.drained(45000);
        const response = await stopMeetingApiV1MeetingsMeetingIdStopPost({
            path: { meeting_id: meeting.id },
            body: { last_seq: lastSeq, captured_ms: capturedMs },
        });
        if (response.error) {
            setProblem(detailFromResult(response, "Could not stop the meeting. Try again."));
            setPhase("capturing");
            return;
        }
        router.push(`/meetings/${meeting.id}`);
    };

    const upload = async () => {
        if (!file) return;
        const limit = (caps?.max_upload_mb ?? 50) * 1024 * 1024;
        if (file.size > limit) {
            setProblem(`That file is larger than ${caps?.max_upload_mb ?? 50} MB.`);
            return;
        }
        setBusy(true);
        setProblem(null);
        const made = await create("upload");
        if (!made) {
            setBusy(false);
            return;
        }
        const response = await uploadRecordingApiV1MeetingsMeetingIdUploadPost({
            path: { meeting_id: made.id },
            body: { file },
        });
        setBusy(false);
        if (response.error) {
            setProblem(detailFromResult(response, "The recording could not be uploaded"));
            return;
        }
        router.push(`/meetings/${made.id}`);
    };

    const saveNotes = async () => {
        setBusy(true);
        setProblem(null);
        const made = await create("notes");
        setBusy(false);
        if (made) router.push(`/meetings/${made.id}`);
    };

    const languages = caps?.languages ?? [];
    const back = threadId ? `/overview?thread=${encodeURIComponent(threadId)}` : "/overview";

    const header = (
        <div className="mb-4 flex items-center gap-2">
            <Button asChild variant="ghost" className="motion-m1 min-h-11 px-2 md:min-h-9">
                <Link href={back}>
                    <ArrowLeft aria-hidden />
                    Chat
                </Link>
            </Button>
        </div>
    );

    if (capsError) {
        return (
            <div className="mx-auto w-full max-w-[640px] px-4 py-6 md:px-6">
                {header}
                <ErrorState title="Could not check what is set up" description={capsError} onRetry={() => void loadCaps()} />
            </div>
        );
    }
    if (!caps) {
        return (
            <div className="mx-auto w-full max-w-[640px] px-4 py-6 md:px-6" aria-busy>
                {header}
                <Skeleton className="mb-3 h-8 w-48" />
                <Skeleton className="mb-2 h-20 w-full" />
                <Skeleton className="mb-2 h-20 w-full" />
                <Skeleton className="h-20 w-full" />
            </div>
        );
    }

    if (phase === "choose") {
        return (
            <div className="mx-auto w-full max-w-[640px] px-4 py-6 md:px-6">
                {header}
                <h1 className="text-2xl font-semibold leading-8">Meeting mode</h1>
                <p className="mt-1 text-sm text-muted-foreground">{caps.limits_note}</p>
                <ul className="mt-5 flex flex-col gap-3">
                    {CHOICES.map(({ source: which, title: label, detail, Icon }) => {
                        const state = offer(which);
                        const available = state?.state === "available";
                        return (
                            <li key={which}>
                                <button
                                    type="button"
                                    disabled={!available}
                                    onClick={() => choose(which)}
                                    className={cn(
                                        "motion-m1 flex min-h-16 w-full items-start gap-3 rounded-[8px] border border-[#7B8491]/50 bg-card p-4 text-left",
                                        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#245B9A]",
                                        available ? "hover:bg-muted/50" : "cursor-not-allowed opacity-80",
                                    )}
                                    data-testid={`choice-${which}`}
                                    aria-describedby={`choice-${which}-detail`}
                                >
                                    <Icon aria-hidden className="mt-0.5 h-5 w-5 shrink-0" />
                                    <span className="min-w-0">
                                        <span className="block font-medium">{label}</span>
                                        <span id={`choice-${which}-detail`} className="block text-sm text-muted-foreground">
                                            {available ? detail : (state?.reason ?? "Needs setup: not available here yet.")}
                                        </span>
                                    </span>
                                </button>
                            </li>
                        );
                    })}
                </ul>
                {caps.summary.state !== "available" && (
                    <p className="mt-4 text-sm text-[#705500] dark:text-amber-300" role="status">
                        {caps.summary.reason}
                    </p>
                )}
                <p className="mt-4 text-sm">
                    <Link className="inline-flex min-h-11 items-center underline underline-offset-4" href="/meetings">
                        Your meetings
                    </Link>
                </p>
            </div>
        );
    }

    if (phase === "setup" && source) {
        const audio = source !== "notes";
        return (
            <div className="mx-auto w-full max-w-[640px] px-4 py-6 pb-[calc(env(safe-area-inset-bottom)+1.5rem)] md:px-6">
                <div className="mb-4">
                    <Button type="button" variant="ghost" className="motion-m1 min-h-11 px-2 md:min-h-9" onClick={() => setPhase("choose")}>
                        <ArrowLeft aria-hidden />
                        Back
                    </Button>
                </div>
                <h1 className="text-2xl font-semibold leading-8">{CHOICES.find((c) => c.source === source)?.title}</h1>
                <p className="mt-1 text-sm text-muted-foreground">
                    Audio source: <strong className="text-foreground">{SOURCE_LINE[source]}</strong>
                </p>
                <form
                    className="mt-5 flex flex-col gap-4"
                    onSubmit={(event) => {
                        event.preventDefault();
                        if (source === "microphone") void startRecording();
                        else if (source === "upload") void upload();
                        else void saveNotes();
                    }}
                >
                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor="meeting-title">Title</Label>
                        <Input id="meeting-title" className="h-11 text-base" value={title} maxLength={200} placeholder="Optional" onChange={(e) => setTitle(e.target.value)} />
                    </div>
                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor="meeting-language">Language spoken</Label>
                        <select
                            id="meeting-language"
                            className="h-11 rounded-[8px] border border-[#7B8491] bg-background px-3 text-base"
                            value={language}
                            onChange={(e) => setLanguage(e.target.value)}
                        >
                            {languages.map((lang) => (
                                <option key={lang.code} value={lang.code}>
                                    {lang.code === "unknown" ? lang.native : `${lang.native} (${lang.english})`}
                                </option>
                            ))}
                        </select>
                        <p className="text-xs text-muted-foreground">Transcribed by Sarvam in the language spoken.</p>
                    </div>
                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor="meeting-people">Participants, if you know them</Label>
                        <Input id="meeting-people" className="h-11 text-base" value={people} placeholder="Priya, Ravi" onChange={(e) => setPeople(e.target.value)} />
                    </div>
                    {source === "notes" && (
                        <div className="flex flex-col gap-1.5">
                            <Label htmlFor="meeting-notes">Notes</Label>
                            <Textarea id="meeting-notes" className="min-h-48 text-base" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Paste or type what was said and agreed." />
                        </div>
                    )}
                    {source === "upload" && (
                        <div className="flex flex-col gap-1.5">
                            <input
                                ref={filePicker}
                                type="file"
                                accept="audio/*,video/mp4,video/webm"
                                className="hidden"
                                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                            />
                            <Button type="button" variant="outline" className="motion-m1 min-h-11 w-fit" onClick={() => filePicker.current?.click()}>
                                <FileAudio aria-hidden />
                                {file ? "Choose another recording" : "Choose a recording"}
                            </Button>
                            <p className="text-sm text-muted-foreground" data-testid="upload-file">
                                {file
                                    ? `${file.name} (${(file.size / (1024 * 1024)).toFixed(1)} MB)`
                                    : `Up to ${caps.max_upload_mb} MB and ${caps.max_minutes} minutes.`}
                            </p>
                        </div>
                    )}
                    {audio && (
                        <div className="flex items-start gap-3 rounded-[8px] border border-[#7B8491]/50 p-3">
                            <Checkbox id="meeting-consent" className="mt-0.5 size-5" checked={consent} onCheckedChange={(v) => setConsent(v === true)} />
                            <Label htmlFor="meeting-consent" className="block text-sm font-normal leading-5">
                                Everyone in this meeting has agreed to it being recorded and transcribed.
                            </Label>
                        </div>
                    )}
                    {audio && <p className="text-xs text-muted-foreground">{caps.retention_note}</p>}
                    {problem && (
                        <p role="alert" className="flex items-start gap-2 text-sm text-[#772322] dark:text-red-300">
                            <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                            {problem}
                        </p>
                    )}
                    <div className="sticky bottom-0 -mx-4 border-t border-border bg-background px-4 py-3 md:static md:mx-0 md:border-0 md:px-0">
                        <Button
                            type="submit"
                            className="motion-m1 min-h-11 w-full md:w-auto"
                            disabled={
                                busy ||
                                (audio && !consent) ||
                                (source === "upload" && !file) ||
                                (source === "notes" && !notes.trim())
                            }
                        >
                            {busy && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                            {source === "microphone" ? "Start recording" : source === "upload" ? (busy ? "Uploading…" : "Upload and transcribe") : "Save notes"}
                        </Button>
                    </div>
                </form>
            </div>
        );
    }

    // Recording (and finishing).
    const finishing = phase === "finishing";
    const stateLabel = finishing
        ? "Saving…"
        : recState === "paused"
          ? "Paused"
          : recState === "interrupted"
            ? "Interrupted"
            : "Recording";
    const lang = languages.find((l) => l.code === meeting?.language);
    return (
        <div className="mx-auto flex w-full max-w-[760px] flex-col px-4 pb-40 pt-4 md:px-6 md:pb-10">
            <p className="text-sm text-muted-foreground" data-testid="capture-source">
                {SOURCE_LINE.microphone} · {lang ? lang.native : "Detect automatically"} · Consent confirmed
            </p>
            <h1 className="mt-1 break-words text-xl font-semibold">{meeting?.title}</h1>
            <div className="mt-4 flex flex-col items-start gap-2">
                <p role="status" aria-live="polite" className={cn("text-sm font-medium", recState === "interrupted" && "text-[#705500] dark:text-amber-300")}>
                    {stateLabel}
                </p>
                <p className="font-mono text-5xl tabular-nums" aria-label={`Captured ${clock(elapsed)}`} data-testid="capture-timer">
                    {clock(elapsed)}
                </p>
                <LevelMeterBound stream={stream} active={recState === "recording" && !finishing} />
                {recState === "interrupted" && (
                    <div className="flex flex-col gap-2 text-sm" role="alert">
                        <p>The microphone stopped. What was captured is kept, and the gap will be marked on the record.</p>
                        <Button type="button" variant="outline" className="motion-m1 min-h-11 w-fit" onClick={() => void reconnect()}>
                            Reconnect microphone
                        </Button>
                    </div>
                )}
                {(waiting > 0 || !online || lost > 0) && (
                    <p className="text-sm text-muted-foreground" role="status">
                        {!online && "Offline: parts are kept here and sent when you reconnect. "}
                        {waiting > 0 && `${waiting} part${waiting === 1 ? "" : "s"} waiting to send. `}
                        {lost > 0 && `${lost} part${lost === 1 ? "" : "s"} could not be sent and will show as gaps.`}
                    </p>
                )}
                {problem && <p role="alert" className="text-sm text-[#772322] dark:text-red-300">{problem}</p>}
            </div>
            {/* Controls: fixed above the home indicator on a phone, inline on
                desktop. The keyboard is never open here. */}
            <div className="fixed inset-x-0 bottom-0 z-20 flex gap-2 border-t border-border bg-background px-4 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] pt-3 md:static md:mt-5 md:border-0 md:p-0">
                {recState === "paused" ? (
                    <Button type="button" variant="outline" className="motion-m1 min-h-11 flex-1 md:flex-none" disabled={finishing} onClick={() => void resume()}>
                        <Play aria-hidden />
                        Resume
                    </Button>
                ) : (
                    <Button type="button" variant="outline" className="motion-m1 min-h-11 flex-1 md:flex-none" disabled={finishing || recState !== "recording"} onClick={() => void pause()}>
                        <Pause aria-hidden />
                        Pause
                    </Button>
                )}
                <Button type="button" className="motion-m1 min-h-11 flex-1 md:flex-none" disabled={finishing} onClick={() => void stop()}>
                    {finishing ? <Loader2 aria-hidden className="motion-continuous animate-spin" /> : <Square aria-hidden />}
                    {finishing ? "Saving…" : "Stop and save"}
                </Button>
            </div>
            {meeting && meeting.possible_actions.length > 0 && (
                <details className="mt-5 rounded-[8px] border border-border p-3 text-sm">
                    <summary className="min-h-11 cursor-pointer content-center md:min-h-0">
                        Possible actions so far ({meeting.possible_actions.length})
                    </summary>
                    <ul className="mt-2 flex flex-col gap-1 text-muted-foreground">
                        {meeting.possible_actions.map((item) => (
                            <li key={item.seq}>{item.text}</li>
                        ))}
                    </ul>
                    <p className="mt-2 text-xs text-muted-foreground">Nothing is done until you review the actions after the meeting.</p>
                </details>
            )}
            <section className="mt-5" aria-label="Live transcript">
                <h2 className="mb-2 text-sm font-semibold">Live transcript</h2>
                <TranscriptView parts={meeting?.transcript ?? []} breaks={meeting?.breaks ?? []} live />
            </section>
        </div>
    );
}

function LevelMeterBound({ stream, active }: { stream: MediaStream | null; active: boolean }) {
    const level = useInputLevel(stream, active);
    return useMemo(() => <LevelMeter level={level} active={active} />, [level, active]);
}

export default MeetingCapture;
