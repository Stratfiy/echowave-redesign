"use client";

/**
 * What each version of this agent actually achieved.
 *
 * The number the product is sold on — a call is finished when the record
 * exists — and until this card it was computable and invisible. "We changed
 * the prompt and bookings fell" was a story somebody told; here it is a number
 * beside the version that caused it.
 *
 * Read-only on purpose. It sits under the steps it measures so the person
 * changing them can see what the last change did, without a detour to a
 * reporting screen they would have to remember exists.
 */

import { useEffect, useState } from "react";

import { outcomeRateApiV1WorkflowWorkflowIdOutcomeRateGet } from "@/client/sdk.gen";
import type { VersionOutcome } from "@/client/types.gen";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/** Below this, a rate is noise rather than a measurement.
 *
 * Ten calls is not much of a sample and a version with three will read 33% or
 * 67% on one call going differently. Showing the count instead of a percentage
 * is the honest rendering: it says "not enough to tell yet" without pretending
 * to a precision the data does not have.
 */
const ENOUGH_CALLS_TO_JUDGE = 10;

function Rate({ version }: { version: VersionOutcome }) {
    if (!version.calls) {
        return <span className="text-muted-foreground">Not used yet</span>;
    }
    if (version.calls < ENOUGH_CALLS_TO_JUDGE) {
        return (
            <span className="text-muted-foreground">
                {version.calls_with_outcome} of {version.calls} &mdash; too few to tell
            </span>
        );
    }
    return (
        <span className="font-medium">
            {Math.round((version.outcome_rate ?? 0) * 100)}%
            <span className="ml-1 text-xs font-normal text-muted-foreground">
                ({version.calls_with_outcome} of {version.calls})
            </span>
        </span>
    );
}

export function OutcomeRateCard({ workflowId }: { workflowId: number }) {
    const [versions, setVersions] = useState<VersionOutcome[] | null>(null);

    useEffect(() => {
        let cancelled = false;
        outcomeRateApiV1WorkflowWorkflowIdOutcomeRateGet({
            path: { workflow_id: workflowId },
        })
            .then((response) => {
                if (!cancelled) setVersions(response.data?.versions ?? []);
            })
            .catch(() => {
                if (!cancelled) setVersions([]);
            });
        return () => {
            cancelled = true;
        };
    }, [workflowId]);

    return (
        <Card id="outcome-rate">
            <CardHeader>
                <CardTitle className="text-base">How it is doing</CardTitle>
                <CardDescription>
                    Calls that ended with something actually written or sent, by
                    version, over the last 30 days. A call counts here only when one
                    of its after-call steps reached an outside app and succeeded.
                </CardDescription>
            </CardHeader>
            <CardContent>
                {versions === null ? (
                    <p className="text-sm text-muted-foreground">Loading&hellip;</p>
                ) : versions.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                        No calls yet on any published version.
                    </p>
                ) : (
                    <div className="space-y-1">
                        {versions.map((version) => (
                            <div
                                key={version.definition_id ?? version.version_number}
                                className="flex items-center justify-between border-b py-2 text-sm last:border-0"
                            >
                                <span className="text-muted-foreground">
                                    Version {version.version_number ?? "—"}
                                </span>
                                <Rate version={version} />
                            </div>
                        ))}
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
