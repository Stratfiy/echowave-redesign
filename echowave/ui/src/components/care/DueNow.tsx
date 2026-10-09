"use client";

/**
 * "Time for your medicine", at the top of Care (launch stream `care`).
 *
 * A reminder in Decibyl needs no phone: this is where it shows. Every dose
 * waiting for an answer -- a reminder shown, or a call still ringing -- with
 * one large "I took it". Nothing shows when nothing is due.
 */

import { Pill } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { markTakenApiV1CareMedicinesMedicineIdTakenPost, myMedicinesApiV1CareMedicinesGet } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

import { localTime } from "./MedicinesPanel";

type Due = { medicineId: number; label: string; dueAt: string; timezone: string };

export function DueNow() {
    const { user, loading } = useAuth();
    const signedIn = Boolean(user);
    const [due, setDue] = useState<Due[]>([]);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        const response = await myMedicinesApiV1CareMedicinesGet();
        if (!response.data) return;
        setDue(
            response.data.medicines.flatMap((m) =>
                (m.doses ?? [])
                    .filter((d) => (d.state === "reminded" || d.state === "calling") && !d.taken_in_app)
                    .map((d) => ({ medicineId: m.id, label: m.label, dueAt: d.due_at, timezone: m.timezone })),
            ),
        );
    }, []);

    useEffect(() => {
        if (loading || !signedIn) return;
        void load();
    }, [loading, signedIn, load]);

    if (due.length === 0) return null;
    return (
        <section aria-label="Due now" className="flex flex-col gap-3" data-testid="care-due-now">
            {due.map((dose) => (
                <div key={`${dose.medicineId}-${dose.dueAt}`} className="flex flex-col gap-3 rounded-2xl border-2 border-foreground p-4">
                    <p className="flex items-start gap-3 text-xl font-semibold">
                        <Pill aria-hidden className="mt-1 h-6 w-6 shrink-0" />
                        Time for {dose.label} ({localTime(dose.dueAt, dose.timezone)})
                    </p>
                    <Button
                        type="button"
                        className="min-h-14 text-lg"
                        onClick={async () => {
                            const response = await markTakenApiV1CareMedicinesMedicineIdTakenPost({
                                path: { medicine_id: dose.medicineId },
                                body: { due_at: dose.dueAt },
                            });
                            if (response.error) {
                                setError(detailFromResult(response, "That was not saved. Try again."));
                                return;
                            }
                            setError(null);
                            void load();
                        }}
                    >
                        I took it
                    </Button>
                </div>
            ))}
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
        </section>
    );
}

export default DueNow;
