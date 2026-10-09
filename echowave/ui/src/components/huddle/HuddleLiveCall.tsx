"use client";

/**
 * During a live call, the huddle is that call's whisper channel.
 *
 * While the agent is on a live call that this person may listen to (live
 * supervision on, listening allowed, an admin or the agent's owner), what
 * they say in the huddle goes to that call as a whisper -- the agent hears
 * it, the caller never does -- and the panel says so: "This call only".
 * Typing works too, for a line that should not be said aloud. With the call
 * over, the huddle is an ordinary huddle again. Nothing shows while
 * `live_supervision` is off. See api/services/huddle/live_call.py.
 */

import { Lock, Send } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { huddleLiveCallApiV1HuddleWorkflowIdLiveCallGet, huddleWhisperApiV1HuddleWorkflowIdWhisperPost } from "@/client/sdk.gen";
import type { HuddleLiveCall as LiveCall } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";

/** How often the panel asks whether the agent is (still) on a call. */
export const LIVE_CALL_POLL_MS = 5000;

export const THIS_CALL_ONLY_COPY =
    "What you say here goes to the agent's live call as a whisper. The caller never hears it.";

export function HuddleLiveCall({ workflowId, active }: { workflowId: number; active: boolean }) {
    const on = useFeature("live_supervision");
    const { user, loading: authLoading } = useAuth();
    const [call, setCall] = useState<LiveCall | null>(null);
    const [text, setText] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [sent, setSent] = useState<string[]>([]);

    const load = useCallback(async () => {
        const response = await huddleLiveCallApiV1HuddleWorkflowIdLiveCallGet({ path: { workflow_id: workflowId } });
        if (response.error) {
            setCall(null);
            return;
        }
        setCall(response.data ?? null);
    }, [workflowId]);

    useEffect(() => {
        if (!on || !active || authLoading || !user) return;
        void load();
        const timer = setInterval(() => void load(), LIVE_CALL_POLL_MS);
        return () => clearInterval(timer);
    }, [on, active, authLoading, user, load]);

    if (!on || !active || !call) return null;

    if (!call.live) {
        if ((call.calls ?? 0) > 1) {
            return (
                <p className="mx-4 mb-3 text-xs text-muted-foreground" data-testid="huddle-live-several">
                    The agent is on {call.calls} live calls. Open one from the live list to whisper to it.
                </p>
            );
        }
        return null;
    }

    const send = async () => {
        const line = text.trim();
        if (!line) return;
        setBusy(true);
        setError(null);
        const response = await huddleWhisperApiV1HuddleWorkflowIdWhisperPost({
            path: { workflow_id: workflowId },
            body: { text: line },
        });
        setBusy(false);
        if (response.error) {
            setError(detailFromError(response.error, "Could not send that to the call."));
            void load();
            return;
        }
        setText("");
        setSent((was) => [...was, line]);
    };

    return (
        <section
            aria-label="Whisper to the live call"
            className="mx-4 mb-3 flex flex-col gap-2 rounded-[var(--radius)] border border-border p-3 text-sm"
            data-testid="huddle-live-call"
        >
            <div className="flex items-center gap-2">
                <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs font-medium">
                    <Lock className="h-3 w-3" aria-hidden />
                    {call.label || "This call only"}
                </span>
                <span className="truncate text-xs text-muted-foreground">
                    {call.direction === "web" ? "Web call" : call.caller || "Live call"}
                </span>
            </div>
            <p className="text-xs text-muted-foreground">{THIS_CALL_ONLY_COPY}</p>
            {sent.length > 0 && (
                <ul className="space-y-0.5 text-xs text-muted-foreground" aria-label="Sent to the call">
                    {sent.map((line, index) => (
                        <li key={`${index}-${line}`}>Sent: {line}</li>
                    ))}
                </ul>
            )}
            <form
                className="flex items-center gap-2"
                onSubmit={(event) => {
                    event.preventDefault();
                    void send();
                }}
            >
                <input
                    aria-label="Type a whisper to the call"
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                    placeholder="Or type it…"
                    maxLength={500}
                    className="min-w-0 flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
                <Button type="submit" size="sm" disabled={busy || !text.trim()}>
                    <Send className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                    Send
                </Button>
            </form>
            {error && (
                <p role="alert" className="text-xs text-destructive">
                    {error}
                </p>
            )}
        </section>
    );
}
