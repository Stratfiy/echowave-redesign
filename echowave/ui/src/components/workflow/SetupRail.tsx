"use client";

import { Check } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { cn } from "@/lib/utils";

/**
 * What is left before this agent takes real calls.
 *
 * A new account meets a canvas and no sense of what "finished" means, and the
 * two places people actually stop are invisible from inside the editor: an
 * agent nobody has called, and an agent nobody can call because it is on no
 * number. Both look exactly like an agent that is ready.
 *
 * Every step is read from the server, which reads the database. Nothing here
 * remembers what was clicked: a rail driven by clicks marks "tested" for
 * somebody who opened the panel and closed it, stays marked after the agent is
 * rewritten, and knows nothing about the step a colleague did. This one is
 * right after a reload and right again when a number is detached.
 *
 * It disappears when the work is done. A permanent row of ticks is furniture,
 * and the screen has enough of that.
 */

type Step = {
    key: string;
    title: string;
    hint: string;
    done: boolean;
};

type Progress = {
    steps: Step[];
    complete: boolean;
    next_step: string | null;
};

export function SetupRail({ workflowId }: { workflowId: string | number }) {
    const [progress, setProgress] = useState<Progress | null>(null);

    const load = useCallback(async () => {
        const response = await client.get({
            url: `/api/v1/workflow/${workflowId}/setup-progress`,
        });
        if (response.error) {
            // Silent. This is an aid, not the page — a agent that cannot be
            // set up is a problem the editor below will report far better than
            // a broken strip at the top of it.
            setProgress(null);
            return;
        }
        setProgress(response.data as Progress);
    }, [workflowId]);

    useEffect(() => {
        void load();
    }, [load]);

    if (!progress || progress.complete) return null;

    return (
        <nav
            aria-label="Setup progress"
            className="flex flex-wrap items-center gap-x-1 gap-y-2 border-b border-border bg-muted/20 px-6 py-2.5"
        >
            {progress.steps.map((step, index) => {
                const isNext = step.key === progress.next_step;
                return (
                    <div key={step.key} className="flex items-center gap-1">
                        {index > 0 && (
                            <span aria-hidden="true" className="mr-1 h-px w-4 bg-border" />
                        )}
                        <span
                            className={cn(
                                "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs",
                                step.done && "text-muted-foreground",
                                // Only the next one is emphasised. Highlighting
                                // every unfinished step makes a to-do list, and
                                // the point of a rail is that there is one
                                // thing to do next.
                                isNext && "bg-primary/10 font-medium text-foreground",
                                !step.done && !isNext && "text-muted-foreground",
                            )}
                            // The rail is a summary, not a control. Announcing
                            // the state on the step itself keeps that true for
                            // a screen reader as well as a sighted reader.
                            aria-current={isNext ? "step" : undefined}
                        >
                            <StepMark done={step.done} />
                            {step.title}
                        </span>
                        {isNext && (
                            <span className="text-xs text-muted-foreground">— {step.hint}</span>
                        )}
                    </div>
                );
            })}
        </nav>
    );
}

function StepMark({ done }: { done: boolean }) {
    if (done) {
        return (
            <>
                <Check className="h-3.5 w-3.5 text-emerald-600" aria-hidden="true" />
                <span className="sr-only">Done:</span>
            </>
        );
    }
    return (
        <>
            <span
                aria-hidden="true"
                className="h-3.5 w-3.5 rounded-full border border-current opacity-50"
            />
            <span className="sr-only">Still to do:</span>
        </>
    );
}

export default SetupRail;
