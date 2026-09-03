"use client";

/**
 * Whether this deployment can actually take money correctly, right now.
 *
 * The assessment behind this screen is the best operational code in the
 * repository and nothing rendered it. `PRODUCTION-CHECKLIST.md` step 8 says
 * "verify readiness reports zero blockers" against an endpoint an operator had
 * no way to look at.
 *
 * The distinction the screen is built around is the one the assessment makes:
 *
 * **Configuration** is cheap to check and worth little alone — a supplier
 * identity set on a deployment whose worker is dead issues exactly as many
 * invoices as no configuration at all.
 *
 * **Evidence** is what catches a deployment where the code is correct and the
 * outcome is still wrong: captured payments with no tax document against them,
 * providers being billed with no rate on file.
 *
 * So a fresh install shows *unknown*, not *ready*. Reporting ready because
 * nothing has happened yet is the specific dishonesty this whole vocabulary
 * exists to avoid, and a screen that rounded it up to a green tick would put it
 * straight back.
 */

import {
    AlertTriangle,
    CheckCircle2,
    CircleHelp,
    Loader2,
    RefreshCw,
    UserRound,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type Check = {
    key: string;
    title: string;
    /** ready | action_required | unknown | needs_a_human */
    status: string;
    detail: string;
    reference: string;
    remedy: string | null;
};

type Assessment = {
    checks: Check[];
    action_required: number;
    needs_a_human: number;
};

const STATUS = {
    ready: {
        label: "Ready",
        icon: CheckCircle2,
        tone: "text-emerald-600 dark:text-emerald-400",
        chip: "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
    },
    action_required: {
        label: "Action required",
        icon: AlertTriangle,
        tone: "text-red-600 dark:text-red-400",
        chip: "bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-300",
    },
    unknown: {
        label: "Not proven yet",
        icon: CircleHelp,
        tone: "text-muted-foreground",
        chip: "bg-muted text-muted-foreground",
    },
    needs_a_human: {
        label: "Needs a person",
        icon: UserRound,
        tone: "text-amber-600 dark:text-amber-400",
        chip: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
    },
} as const;

function statusOf(status: string) {
    return STATUS[status as keyof typeof STATUS] ?? STATUS.unknown;
}

/** Blocking first, then unproven, then the standing obligations, then the
 *  passes. An operator works this page top to bottom. */
const ORDER = ["action_required", "unknown", "needs_a_human", "ready"];

export default function ReadinessPage() {
    const { user, loading: authLoading } = useAuth();
    const [assessment, setAssessment] = useState<Assessment | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [probing, setProbing] = useState(false);

    const load = useCallback(async (probe: boolean) => {
        if (probe) setProbing(true);
        else setLoading(true);
        const result = await client.get({
            url: "/api/v1/admin/billing/readiness",
            query: probe ? { probe: true } : undefined,
        });
        setLoading(false);
        setProbing(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not read readiness"));
            return;
        }
        setAssessment(result.data as Assessment);
        setError(null);
    }, []);

    useEffect(() => {
        if (authLoading || !user) return;
        void load(false);
    }, [authLoading, user, load]);

    if (loading && !assessment) {
        return (
            <div className="flex h-40 items-center justify-center">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
        );
    }

    if (error && !assessment) {
        return <p className="text-sm text-red-600 dark:text-red-400">{error}</p>;
    }

    if (!assessment) return null;

    const checks = [...assessment.checks].sort(
        (a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status),
    );
    const blocking = assessment.action_required;

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                    <h2 className="text-lg font-medium">Can we take money?</h2>
                    <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                        Properties of this deployment, not of any one account. The
                        two failures worth knowing about do not raise an error:
                        money taken with no tax document issued, and calls billed
                        against an empty price book — which reports 100% margin
                        rather than a problem.
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => void load(false)}
                        disabled={loading}
                    >
                        <RefreshCw
                            className={cn("mr-1.5 h-3.5 w-3.5", loading && "animate-spin")}
                        />
                        Re-check
                    </Button>
                    {/* Opt-in, because it makes an outbound request to our own
                        public webhook URL and this page is otherwise cheap to
                        poll. It is also the only check that proves Razorpay can
                        actually reach us, which nothing else can establish. */}
                    <Button
                        type="button"
                        size="sm"
                        onClick={() => void load(true)}
                        disabled={probing}
                    >
                        {probing ? (
                            <>
                                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                                Probing…
                            </>
                        ) : (
                            "Test the webhook"
                        )}
                    </Button>
                </div>
            </div>

            <div
                className={cn(
                    "rounded-xl border p-5",
                    blocking > 0
                        ? "border-red-200 bg-red-50 dark:border-red-900/50 dark:bg-red-950/30"
                        : "border-emerald-200 bg-emerald-50 dark:border-emerald-900/50 dark:bg-emerald-950/30",
                )}
            >
                <p
                    className={cn(
                        "text-2xl font-semibold tabular-nums",
                        blocking > 0
                            ? "text-red-700 dark:text-red-300"
                            : "text-emerald-700 dark:text-emerald-300",
                    )}
                >
                    {blocking === 0
                        ? "No blockers"
                        : `${blocking} blocker${blocking === 1 ? "" : "s"}`}
                </p>
                <p
                    className={cn(
                        "mt-1 text-sm",
                        blocking > 0
                            ? "text-red-800 dark:text-red-300"
                            : "text-emerald-800 dark:text-emerald-300",
                    )}
                >
                    {blocking === 0
                        ? "Everything that can be checked from here passes. That is not the same as proven — see anything marked “not proven yet” below."
                        : "Each one below is a way this deployment loses money or accrues a liability without erroring."}
                </p>
                {assessment.needs_a_human > 0 && (
                    /* Counted apart on purpose. These never clear, so folding
                       them into the blocker count would make it permanently
                       non-zero and therefore ignored. */
                    <p className="mt-2 text-sm text-muted-foreground">
                        Plus {assessment.needs_a_human} obligation
                        {assessment.needs_a_human === 1 ? "" : "s"} no code can
                        discharge — they never report ready.
                    </p>
                )}
            </div>

            {error && (
                <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
            )}

            <div className="space-y-3">
                {checks.map((check) => {
                    const s = statusOf(check.status);
                    const Icon = s.icon;
                    return (
                        <div
                            key={check.key}
                            className="rounded-xl border bg-card p-4"
                        >
                            <div className="flex items-start gap-3">
                                <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", s.tone)} />
                                <div className="min-w-0 flex-1">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="font-medium">{check.title}</span>
                                        <span
                                            className={cn(
                                                "rounded-full px-2 py-0.5 text-[11px] font-medium",
                                                s.chip,
                                            )}
                                        >
                                            {s.label}
                                        </span>
                                    </div>
                                    <p className="mt-1 text-sm text-muted-foreground">
                                        {check.detail}
                                    </p>
                                    {/* The command or the screen, verbatim. A
                                        check that says what is wrong and not
                                        what to do about it is a check somebody
                                        has to go and research. */}
                                    {check.remedy && (
                                        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-muted/60 p-3 text-xs">
                                            {check.remedy}
                                        </pre>
                                    )}
                                    <p className="mt-2 text-xs text-muted-foreground/80">
                                        {check.reference}
                                    </p>
                                </div>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
