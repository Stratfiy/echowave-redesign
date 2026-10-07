"use client";

/**
 * Help with my phone (launch stream `care`): one step at a time.
 *
 * One step on the screen, in large words, and two answers: "Yes, it
 * worked" and "No, it didn't". No shows another way to try; no again stops
 * kindly and says who in the family was told, if anyone. Each answer names
 * the step's version, so a double tap answers once.
 */

import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import {
    answerHelpApiV1CareHelpSessionsSessionIdAnswerPost,
    helpTopicsApiV1CareHelpTopicsGet,
    startHelpApiV1CareHelpSessionsPost,
} from "@/client/sdk.gen";
import type { HelpSession, HelpTopic } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

import { SpeakButton } from "./SpeakButton";

export function TechHelpPanel() {
    const { user, loading: authLoading } = useAuth();
    const signedIn = Boolean(user);
    const [topics, setTopics] = useState<HelpTopic[] | null>(null);
    const [question, setQuestion] = useState("");
    const [session, setSession] = useState<HelpSession | null>(null);
    const [note, setNote] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (authLoading || !signedIn) return;
        void (async () => {
            const response = await helpTopicsApiV1CareHelpTopicsGet();
            if (response.error || !response.data) {
                setError(detailFromResult(response, "Could not load the topics."));
                setTopics([]);
                return;
            }
            setTopics(response.data.topics);
        })();
    }, [authLoading, signedIn]);

    const start = async (body: { guide?: string; question?: string }) => {
        setBusy(true);
        setError(null);
        setNote(null);
        const response = await startHelpApiV1CareHelpSessionsPost({ body });
        setBusy(false);
        if (response.error || !response.data) {
            setError(detailFromResult(response, "Could not start. Try again."));
            return;
        }
        if (!response.data.matched || !response.data.session) {
            setNote(response.data.note ?? "I do not have steps for that yet.");
            if (response.data.topics?.length) setTopics(response.data.topics);
            return;
        }
        setSession(response.data.session);
    };

    const answer = async (worked: boolean) => {
        if (!session) return;
        setBusy(true);
        setError(null);
        const response = await answerHelpApiV1CareHelpSessionsSessionIdAnswerPost({
            path: { session_id: session.id },
            body: { worked, version: session.version },
        });
        setBusy(false);
        if (response.error || !response.data) {
            setError(detailFromResult(response, "That answer was not saved. Try again."));
            return;
        }
        setSession(response.data);
    };

    if (session) {
        const finished = session.state !== "active";
        return (
            <section className="flex flex-col gap-5" data-testid="help-step" aria-live="polite">
                <div className="flex flex-col gap-1">
                    <p className="text-sm font-medium text-muted-foreground">{session.title}</p>
                    {!finished && (
                        <p className="text-sm text-muted-foreground">
                            Step {session.step_number} of {session.steps_total}
                            {session.is_alternative ? " · another way" : ""}
                        </p>
                    )}
                </div>
                {session.note && <p className="text-lg font-medium">{session.note}</p>}
                {!finished && <p className="text-2xl font-semibold leading-snug">{session.say}</p>}
                {error && (
                    <p role="alert" className="text-sm text-destructive">
                        {error}
                    </p>
                )}
                {!finished ? (
                    <div className="flex flex-col gap-3">
                        <p className="text-lg">Did that work?</p>
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                            <Button type="button" className="motion-m1 min-h-14 text-lg" disabled={busy} onClick={() => void answer(true)}>
                                {busy && <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />}
                                Yes, it worked
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                className="motion-m1 min-h-14 text-lg"
                                disabled={busy}
                                onClick={() => void answer(false)}
                            >
                                No, it didn&apos;t
                            </Button>
                        </div>
                    </div>
                ) : (
                    <Button
                        type="button"
                        className="motion-m1 min-h-12 self-start px-6 text-base"
                        onClick={() => {
                            setSession(null);
                            setQuestion("");
                        }}
                    >
                        Help with something else
                    </Button>
                )}
            </section>
        );
    }

    return (
        <section className="flex flex-col gap-5" data-testid="help-topics">
            <label className="flex flex-col gap-2">
                <span className="font-medium">What do you want to do?</span>
                <input
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    maxLength={500}
                    className="min-h-12 w-full rounded-lg border border-input bg-background px-3 text-base focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    placeholder="For example: make the writing bigger"
                    onKeyDown={(event) => {
                        if (event.key === "Enter" && question.trim()) void start({ question });
                    }}
                />
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
                <Button
                    type="button"
                    className="motion-m1 min-h-12 text-base"
                    disabled={busy || !question.trim()}
                    onClick={() => void start({ question })}
                >
                    Show me how
                </Button>
                <SpeakButton onText={(said) => setQuestion(said)} />
            </div>
            {note && <p className="text-base">{note}</p>}
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
            <div>
                <h3 className="mb-2 font-semibold">Or choose one</h3>
                {topics === null ? (
                    <p className="text-sm text-muted-foreground">Loading…</p>
                ) : (
                    <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                        {topics.map((topic) => (
                            <li key={topic.slug}>
                                <button
                                    type="button"
                                    disabled={busy}
                                    onClick={() => void start({ guide: topic.slug })}
                                    className="motion-m1 flex min-h-12 w-full items-center rounded-lg border border-border px-4 py-2 text-left text-base hover:bg-muted/50"
                                >
                                    {topic.title}
                                </button>
                            </li>
                        ))}
                    </ul>
                )}
            </div>
        </section>
    );
}

export default TechHelpPanel;
