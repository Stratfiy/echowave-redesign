"use client";

/**
 * Putting the terms in front of somebody before they commit to paying for
 * something, and recording that they saw them.
 *
 * A click-wrap is enforceable in India under IT Act s10A given three things:
 * reasonable notice, an affirmative act, and terms accessible *before*
 * acceptance. All three are worthless without a record — in a dispute the
 * question is never whether the terms were good, it is whether this customer,
 * on this date, agreed to this version. The server writes that record; this is
 * the notice and the affirmative act.
 *
 * Deliberately not a checkbox tucked under a button. Each required document
 * gets its own tick and its own link that opens the actual text, because "I
 * agree to the Terms and DPA" as one control is the pattern courts treat least
 * kindly and the one customers read least.
 *
 * Nothing here decides whether the account may proceed. It asks the server what
 * is outstanding and asks again after accepting, because a screen that marks
 * itself compliant is a screen that disagrees with the thing that decides.
 */

import { ExternalLink, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    acceptAgreementApiV1PrivacyAgreementsAcceptPost,
    listAgreementsApiV1PrivacyAgreementsGet,
} from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { detailFromResult } from "@/lib/apiError";

type Agreement = {
    key: string;
    title: string;
    version: string;
    url: string;
    required: boolean;
};

export type AgreementsState = {
    agreements: Agreement[];
    outstanding: string[];
    loading: boolean;
    refresh: () => Promise<void>;
};

/** What this account still owes, asked of the server rather than assumed. */
export function useAgreements(enabled: boolean): AgreementsState {
    const [agreements, setAgreements] = useState<Agreement[]>([]);
    const [outstanding, setOutstanding] = useState<string[]>([]);
    const [loading, setLoading] = useState(true);

    const refresh = useCallback(async () => {
        const response = await listAgreementsApiV1PrivacyAgreementsGet();
        if (response.data) {
            const data = response.data as unknown as {
                agreements: Agreement[];
                outstanding: string[];
            };
            setAgreements(data.agreements ?? []);
            setOutstanding(data.outstanding ?? []);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (!enabled) return;
        void refresh();
    }, [enabled, refresh]);

    return { agreements, outstanding, loading, refresh };
}

export function AgreementsDialog({
    open,
    state,
    onOpenChange,
    onAccepted,
    reason,
}: {
    open: boolean;
    state: AgreementsState;
    onOpenChange: (open: boolean) => void;
    onAccepted: () => void;
    /** Why they are being asked now. Written by the caller because "before you
     * buy a number" and "before you start a campaign" are different promises. */
    reason: string;
}) {
    const [ticked, setTicked] = useState<Record<string, boolean>>({});
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const required = state.agreements.filter((a) =>
        state.outstanding.includes(a.key),
    );
    const allTicked = required.length > 0 && required.every((a) => ticked[a.key]);

    const handleAccept = async () => {
        setIsSubmitting(true);
        setError(null);
        // One request per document, because that is how they are recorded: one
        // row per agreement per version, never updated and never deleted.
        for (const agreement of required) {
            const response = await acceptAgreementApiV1PrivacyAgreementsAcceptPost({
                body: { agreement: agreement.key },
            });
            if (response.error) {
                setError(
                    detailFromResult(response, "Could not record your acceptance."),
                );
                setIsSubmitting(false);
                return;
            }
        }
        await state.refresh();
        setIsSubmitting(false);
        onOpenChange(false);
        onAccepted();
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <ShieldCheck className="h-5 w-5 text-primary" />
                        Before you continue
                    </DialogTitle>
                    <DialogDescription>{reason}</DialogDescription>
                </DialogHeader>

                {error && (
                    <div
                        role="alert"
                        className="rounded-[var(--radius-control)] border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
                    >
                        {error}
                    </div>
                )}

                <div className="space-y-3">
                    {required.map((agreement) => (
                        <label
                            key={agreement.key}
                            className="flex cursor-pointer items-start gap-3 rounded-[var(--radius-control)] border border-border p-3"
                        >
                            <Checkbox
                                checked={Boolean(ticked[agreement.key])}
                                onCheckedChange={(value) =>
                                    setTicked((current) => ({
                                        ...current,
                                        [agreement.key]: value === true,
                                    }))
                                }
                                className="mt-0.5"
                            />
                            <span className="space-y-1 text-sm">
                                <span className="block font-medium">
                                    I agree to the {agreement.title}
                                </span>
                                <a
                                    href={agreement.url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="inline-flex items-center gap-1 text-xs text-muted-foreground underline"
                                    onClick={(event) => event.stopPropagation()}
                                >
                                    Read it (version {agreement.version})
                                    <ExternalLink className="h-3 w-3" />
                                </a>
                            </span>
                        </label>
                    ))}
                </div>

                <p className="text-xs text-muted-foreground">
                    We record which version you accepted, when, and from where.
                    If a document changes in a way that alters what you are
                    agreeing to, we will ask again.
                </p>

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        Not now
                    </Button>
                    <Button
                        onClick={() => void handleAccept()}
                        disabled={!allTicked || isSubmitting}
                    >
                        Agree and continue
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
