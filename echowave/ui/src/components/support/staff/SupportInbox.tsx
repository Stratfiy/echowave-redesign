"use client";

/**
 * Screen 32: the support inbox and case, in the staff console.
 *
 * At 1280 px and wider: queue (280 px), the case thread, and the customer
 * context (300 px) side by side. From 1024 px the context becomes a drawer.
 * On a phone, queue, thread and context are three screens: the queue at
 * /superadmin/support, the case at /superadmin/support/<id> with Back, and
 * the context as a full-height sheet. Every case has its own deep link.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { getAuthUserApiV1UserAuthUserGet } from "@/client/sdk.gen";
import { EmptyState, ErrorState } from "@/components/shell";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useFeature } from "@/lib/features";
import {
    loadCase,
    loadQueue,
    loadStaff,
    type QueueRow,
    type StaffMember,
    type SupportCase,
} from "@/lib/support/staff";
import { cn } from "@/lib/utils";

import { CaseContext } from "./CaseContext";
import { SupportCaseThread } from "./SupportCaseThread";
import { type QueueFilters, SupportQueue } from "./SupportQueue";

export function SupportInbox({ selectedId }: { selectedId: number | null }) {
    const actionsOn = useFeature("support_actions");
    const [filters, setFilters] = useState<QueueFilters>({ status: "active", assignee: "", severity: "", overdue: false });
    const [rows, setRows] = useState<QueueRow[] | null>(null);
    const [queueError, setQueueError] = useState<string | null>(null);
    const [loadedAt, setLoadedAt] = useState<Date | null>(null);
    const [staff, setStaff] = useState<StaffMember[]>([]);
    const [me, setMe] = useState<number | null>(null);
    const [supportCase, setSupportCase] = useState<SupportCase | null>(null);
    const [caseError, setCaseError] = useState<string | null>(null);
    const [contextOpen, setContextOpen] = useState(false);
    const lastCase = useRef<number | null>(null);

    const refreshQueue = useCallback(async (next: QueueFilters) => {
        const outcome = await loadQueue(next);
        if (outcome.ok) {
            setRows(outcome.value);
            setQueueError(null);
            setLoadedAt(new Date());
        } else {
            // A failed refresh keeps the last list, labelled (design "Freshness").
            setQueueError(outcome.error);
        }
    }, []);

    const refreshCase = useCallback(async () => {
        if (selectedId === null) return;
        const outcome = await loadCase(selectedId);
        if (outcome.ok) {
            setSupportCase(outcome.value);
            setCaseError(null);
        } else setCaseError(outcome.error);
    }, [selectedId]);

    useEffect(() => {
        void refreshQueue(filters);
    }, [filters, refreshQueue]);

    useEffect(() => {
        void loadStaff().then((outcome) => outcome.ok && setStaff(outcome.value));
        void getAuthUserApiV1UserAuthUserGet().then((response) => setMe(response.data?.id ?? null));
    }, []);

    useEffect(() => {
        if (lastCase.current !== selectedId) {
            // A different case: clear the previous one before showing the next.
            setSupportCase(null);
            setCaseError(null);
            lastCase.current = selectedId;
        }
        void refreshCase();
    }, [selectedId, refreshCase]);

    const staffNames = Object.fromEntries(staff.map((s) => [s.id, s.email ?? `Staff #${s.id}`]));
    const afterChange = async () => {
        await Promise.all([refreshCase(), refreshQueue(filters)]);
    };

    const caseView =
        selectedId === null ? (
            <EmptyState title="Choose a case from the queue." />
        ) : caseError && !supportCase ? (
            <ErrorState title="This case could not load." description={caseError} onRetry={() => void refreshCase()} />
        ) : !supportCase ? (
            <p role="status" className="p-4 text-sm text-muted-foreground">
                Loading the case…
            </p>
        ) : (
            <SupportCaseThread
                key={supportCase.id}
                supportCase={supportCase}
                staff={staff}
                me={me}
                onChanged={afterChange}
                onOpenContext={() => setContextOpen(true)}
                className="h-full"
            />
        );

    return (
        <div className="flex h-[calc(100dvh-7rem)] min-h-[32rem] w-full overflow-hidden border-t border-border" data-testid="support-inbox">
            <SupportQueue
                rows={rows}
                error={queueError}
                loadedAt={loadedAt}
                filters={filters}
                onFilters={setFilters}
                onRefresh={() => void refreshQueue(filters)}
                selectedId={selectedId}
                staffNames={staffNames}
                className={cn(
                    "w-full border-r border-border lg:w-[280px] lg:shrink-0",
                    selectedId !== null && "hidden lg:flex",
                )}
            />
            <div className={cn("min-w-0 flex-1", selectedId === null && "hidden lg:block")}>{caseView}</div>
            {supportCase && (
                <>
                    <CaseContext supportCase={supportCase} actionsOn={actionsOn} className="hidden w-[300px] shrink-0 border-l border-border xl:flex" />
                    <Sheet open={contextOpen} onOpenChange={setContextOpen}>
                        <SheetContent side="right" className="w-full max-w-full overflow-y-auto p-0 sm:max-w-[360px]">
                            <SheetHeader className="border-b border-border">
                                <SheetTitle>Customer context</SheetTitle>
                            </SheetHeader>
                            <CaseContext supportCase={supportCase} actionsOn={actionsOn} />
                        </SheetContent>
                    </Sheet>
                </>
            )}
        </div>
    );
}

export default SupportInbox;
