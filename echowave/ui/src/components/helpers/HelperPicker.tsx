"use client";

/**
 * Screen 06: the helper picker in Chat.
 *
 * A compact 360px menu on a desktop, a full-height sheet with a fixed "Use
 * helper" footer on a phone. Automatic is the default; the five helpers and
 * the builder each open a short detail -- what it does, what it needs, what
 * it may read and do, one example -- with its capability state from the
 * server. Only an available helper can be used, and the server checks that
 * again when the message is sent: hiding a button is not authorization.
 *
 * Setting a helper up never leaves Chat: a missing app puts a connect card
 * in this conversation, and the box keeps what was typed.
 */

import { ArrowLeft, Bot, CheckCircle2, ChevronRight, CircleAlert, Hammer, Loader2, Sparkles } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { listHelpersApiV1HelpersGet, setupHelperApiV1HelpersKeySetupPost } from "@/client/sdk.gen";
import type { HelperOut, HelpersResponse } from "@/client/types.gen";
import { TradingInterests } from "@/components/helpers/TradingInterests";
import { ErrorState } from "@/components/shell/ErrorState";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/use-mobile";
import { detailFromResult } from "@/lib/apiError";
import { useFeature } from "@/lib/features";
import { APP_NAMES, AUTOMATIC, HELPER_STATE_LABEL } from "@/lib/helpers";
import { cn } from "@/lib/utils";

export type ChosenHelper = { key: string; name: string };

type Load =
    | { state: "idle" }
    | { state: "loading" }
    | { state: "failed"; message: string }
    | { state: "ready"; data: HelpersResponse };

function StateBadge({ helper }: { helper: HelperOut }) {
    if (helper.state === "available") return null;
    return (
        <span
            className={cn(
                "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
                helper.state === "needs_setup"
                    ? "border-amber-300 text-[#705500] dark:text-amber-300"
                    : "border-border text-muted-foreground",
            )}
            data-testid={`helper-state-${helper.key}`}
        >
            <CircleAlert aria-hidden className="h-3 w-3" />
            {HELPER_STATE_LABEL[helper.state] ?? helper.state}
        </span>
    );
}

export function HelperPicker({
    selected,
    onChoose,
    onExample,
    threadId,
    initialKey,
    disabled,
}: {
    /** The chosen helper's key; null is Automatic. */
    selected: string | null;
    onChoose: (helper: ChosenHelper | null) => void;
    /** Put an example into the box, editable. */
    onExample: (text: string) => void;
    threadId: string | null;
    /** From `?helper=` on the address: chosen once the list arrives, if it
     *  is available. */
    initialKey?: string | null;
    disabled?: boolean;
}) {
    const mobile = useIsMobile();
    const tradingOn = useFeature("trading_summaries");
    const ledgerOn = useFeature("follow_up_ledger");
    const reportsOn = useFeature("research_reports");
    const [open, setOpen] = useState(false);
    const [load, setLoad] = useState<Load>({ state: "idle" });
    const [detail, setDetail] = useState<string | null>(null);
    const [setupNote, setSetupNote] = useState<string | null>(null);
    const [settingUp, setSettingUp] = useState(false);
    const trigger = useRef<HTMLButtonElement | null>(null);
    const appliedInitial = useRef(false);

    const fetchHelpers = useCallback(async () => {
        setLoad({ state: "loading" });
        try {
            const response = await listHelpersApiV1HelpersGet();
            if (response.error || !response.data) {
                setLoad({ state: "failed", message: detailFromResult(response, "Could not load the helpers.") });
                return;
            }
            setLoad({ state: "ready", data: response.data });
        } catch {
            setLoad({ state: "failed", message: "Could not load the helpers. Check your connection." });
        }
    }, []);

    useEffect(() => {
        if (open && load.state === "idle") void fetchHelpers();
    }, [open, load.state, fetchHelpers]);

    // `?helper=builder` from an old "Build an agent" link: chosen if ready,
    // otherwise its detail opens so the reason is read, not guessed.
    useEffect(() => {
        if (!initialKey || appliedInitial.current) return;
        if (load.state === "idle") {
            void fetchHelpers();
            return;
        }
        if (load.state !== "ready") return;
        appliedInitial.current = true;
        const all = [...load.data.helpers, ...(load.data.builder ? [load.data.builder] : [])];
        const found = all.find((h) => h.key === initialKey);
        if (!found) return;
        if (found.state === "available") onChoose({ key: found.key, name: found.name });
        else {
            setDetail(found.key);
            setOpen(true);
        }
    }, [initialKey, load, fetchHelpers, onChoose]);

    const helpers = load.state === "ready" ? load.data.helpers : [];
    const builder = load.state === "ready" ? load.data.builder ?? null : null;
    const shown = detail ? [...helpers, ...(builder ? [builder] : [])].find((h) => h.key === detail) ?? null : null;

    const close = () => {
        setOpen(false);
        setDetail(null);
        setSetupNote(null);
    };
    const choose = (helper: HelperOut | null) => {
        onChoose(helper ? { key: helper.key, name: helper.name } : null);
        close();
    };

    const runSetup = async (helper: HelperOut) => {
        setSettingUp(true);
        setSetupNote(null);
        const response = await setupHelperApiV1HelpersKeySetupPost({
            path: { key: helper.key },
            body: { thread_id: threadId },
        });
        setSettingUp(false);
        if (response.error) {
            setSetupNote(detailFromResult(response, "Could not start the setup."));
            return;
        }
        setSetupNote(
            response.data?.status === "offered"
                ? "A connect card is in this conversation. Your message is still in the box."
                : response.data?.note ?? "Nothing to set up.",
        );
    };

    const row = (helper: HelperOut) => (
        <li key={helper.key}>
            <button
                type="button"
                className={cn(
                    "flex min-h-11 w-full items-center gap-3 rounded-md px-3 py-2 text-left hover:bg-accent focus-visible:outline-2 focus-visible:outline-offset-2",
                    selected === helper.key && "bg-accent",
                )}
                onClick={() => setDetail(helper.key)}
                data-testid={`helper-row-${helper.key}`}
            >
                <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                        <span className="font-medium">{helper.name}</span>
                        {selected === helper.key && <CheckCircle2 aria-label="chosen" className="h-4 w-4" />}
                    </span>
                    <span className="block text-sm text-muted-foreground">{helper.job}</span>
                </span>
                <StateBadge helper={helper} />
                <ChevronRight aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
            </button>
        </li>
    );

    const list = (
        <div className="flex min-h-0 flex-1 flex-col">
            <div className="px-3 pb-2 pt-1">
                <p className="text-sm font-medium">Helpers</p>
                <p className="text-sm text-muted-foreground">Optional. Decibyl picks what fits when you leave it on Automatic.</p>
            </div>
            {load.state === "loading" || load.state === "idle" ? (
                <p className="flex items-center gap-2 px-3 py-4 text-sm text-muted-foreground" role="status">
                    <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> Loading helpers…
                </p>
            ) : load.state === "failed" ? (
                <ErrorState title="Helpers did not load" description={load.message} onRetry={() => void fetchHelpers()} />
            ) : (
                <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto px-1 pb-2" aria-label="Helpers">
                    <li>
                        <button
                            type="button"
                            className={cn(
                                "flex min-h-11 w-full items-center gap-3 rounded-md px-3 py-2 text-left hover:bg-accent",
                                selected === null && "bg-accent",
                            )}
                            onClick={() => choose(null)}
                            data-testid="helper-row-automatic"
                        >
                            <Sparkles aria-hidden className="h-4 w-4 text-muted-foreground" />
                            <span className="min-w-0 flex-1">
                                <span className="flex items-center gap-2 font-medium">
                                    Automatic {selected === null && <CheckCircle2 aria-label="chosen" className="h-4 w-4" />}
                                </span>
                                <span className="block text-sm text-muted-foreground">Decibyl decides which skills a request needs.</span>
                            </span>
                        </button>
                    </li>
                    {helpers.map(row)}
                    {builder && (
                        <>
                            <li className="px-3 pb-1 pt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">Build</li>
                            {row(builder)}
                        </>
                    )}
                </ul>
            )}
        </div>
    );

    const detailView = shown && (
        <div className="flex min-h-0 flex-1 flex-col" data-testid={`helper-detail-${shown.key}`}>
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-3 pb-3">
                <button
                    type="button"
                    className="-ml-1 flex min-h-11 items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
                    onClick={() => {
                        setDetail(null);
                        setSetupNote(null);
                    }}
                >
                    <ArrowLeft aria-hidden className="h-4 w-4" /> All helpers
                </button>
                <div>
                    <h3 className="flex items-center gap-2 text-base font-semibold">
                        {shown.key === "builder" ? <Hammer aria-hidden className="h-4 w-4" /> : <Bot aria-hidden className="h-4 w-4" />}
                        {shown.name}
                    </h3>
                    <p className="text-sm">{shown.job}</p>
                </div>
                <div
                    className={cn(
                        "rounded-md border px-3 py-2 text-sm",
                        shown.state === "available" ? "border-border" : "border-amber-300",
                    )}
                    role="status"
                >
                    <p className="font-medium">{HELPER_STATE_LABEL[shown.state] ?? shown.state}</p>
                    {shown.reason && <p className="text-muted-foreground">{shown.reason}</p>}
                    {shown.notes.map((note) => (
                        <p key={note} className="text-muted-foreground">
                            {note}
                        </p>
                    ))}
                    {shown.setup?.kind === "connect" && (
                        <Button
                            type="button"
                            variant="outline"
                            className="mt-2 min-h-11"
                            disabled={settingUp}
                            onClick={() => void runSetup(shown)}
                        >
                            {settingUp && <Loader2 aria-hidden className="h-4 w-4 animate-spin" />}
                            {shown.setup.label}
                        </Button>
                    )}
                    {shown.setup?.kind === "number" && (
                        <p className="mt-2">
                            <Link className="underline" href="/settings/phone-number">
                                {shown.setup.label}
                            </Link>
                        </p>
                    )}
                    {shown.setup?.kind === "operator" && <p className="mt-1 text-muted-foreground">{shown.setup.label}</p>}
                    {setupNote && (
                        <p className="mt-2" role="status">
                            {setupNote}
                        </p>
                    )}
                </div>
                {shown.connections.length > 0 && (
                    <div>
                        <p className="text-sm font-medium">Needs</p>
                        <p className="text-sm text-muted-foreground">One of: {shown.connections.map((app) => APP_NAMES[app] ?? app).join(", ")}</p>
                    </div>
                )}
                <div>
                    <p className="text-sm font-medium">What it can read and do</p>
                    <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                        {shown.permissions.map((p) => (
                            <li key={p}>{p}</li>
                        ))}
                    </ul>
                    <p className="mt-1 text-sm text-muted-foreground">Choosing a helper never gives it more access than Decibyl has.</p>
                </div>
                <div>
                    <p className="text-sm font-medium">Its promise</p>
                    <p className="text-sm text-muted-foreground">{shown.boundary}</p>
                </div>
                <div>
                    <p className="text-sm font-medium">Try</p>
                    <button
                        type="button"
                        className="mt-1 min-h-11 w-full rounded-md border border-border px-3 py-2 text-left text-sm hover:bg-accent"
                        onClick={() => {
                            onExample(shown.example);
                            if (shown.state === "available") choose(shown);
                            else close();
                        }}
                    >
                        “{shown.example}”
                    </button>
                </div>
                {shown.key === "research" && tradingOn && <TradingInterests threadId={threadId} available={shown.state === "available"} />}
                {shown.key === "research" && reportsOn && (
                    <Link className="block min-h-11 py-2 text-sm underline" href="/saved-reports" onClick={close}>
                        Your saved reports
                    </Link>
                )}
                {shown.key === "follow_up" && ledgerOn && (
                    <Link className="block min-h-11 py-2 text-sm underline" href="/follow-ups" onClick={close}>
                        Who owes me
                    </Link>
                )}
                {shown.key === "builder" && (
                    <Link className="block min-h-11 py-2 text-sm underline" href="/trackers" onClick={close}>
                        Your trackers
                    </Link>
                )}
                {shown.advanced && (
                    <details className="rounded-md border border-border px-3 py-2 text-sm">
                        <summary className="min-h-11 cursor-pointer py-2 font-medium">Advanced</summary>
                        <p className="text-muted-foreground">Model: {shown.advanced.model}</p>
                        <p className="text-muted-foreground">Voice: {shown.advanced.voice}</p>
                        <p className="text-muted-foreground">
                            Skills: {shown.advanced.skills.length ? shown.advanced.skills.join(", ") : "none assigned"}
                        </p>
                        <p className="break-words text-muted-foreground">Tools: {shown.advanced.tools.join(", ")}</p>
                        <Link className="mt-1 inline-block min-h-11 py-2 underline" href="/workflow" onClick={close}>
                            Agent settings
                        </Link>
                    </details>
                )}
            </div>
            <div className="border-t border-border px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3">
                <Button
                    type="button"
                    className="min-h-11 w-full"
                    disabled={shown.state !== "available"}
                    onClick={() => choose(shown)}
                    data-testid="use-helper"
                >
                    {shown.state === "available" ? `Use ${shown.name}` : HELPER_STATE_LABEL[shown.state] ?? "Unavailable"}
                </Button>
            </div>
        </div>
    );

    const body = shown ? detailView : list;
    const label = "Choose a helper";
    const triggerButton = (
        <Button
            ref={trigger}
            type="button"
            variant="ghost"
            size="icon"
            aria-label={label}
            title={label}
            disabled={disabled}
            className="shrink-0 text-muted-foreground max-md:h-11 max-md:w-11"
            onClick={mobile ? () => setOpen(true) : undefined}
            data-testid="helper-picker"
        >
            <Sparkles className="h-4 w-4" />
        </Button>
    );

    if (mobile) {
        return (
            <>
                {triggerButton}
                <Sheet open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>
                    <SheetContent side="bottom" className="flex h-[100dvh] max-h-[100dvh] flex-col gap-0 p-0 pt-12">
                        <SheetTitle className="sr-only">{label}</SheetTitle>
                        <SheetDescription className="sr-only">Automatic or one of the helpers.</SheetDescription>
                        {body}
                    </SheetContent>
                </Sheet>
            </>
        );
    }
    return (
        <Popover open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>
            <PopoverTrigger asChild>{triggerButton}</PopoverTrigger>
            <PopoverContent
                side="top"
                align="start"
                className="motion-m4-enter flex max-h-[min(36rem,80vh)] w-[360px] flex-col p-0 pt-2"
                onCloseAutoFocus={(event) => {
                    // Focus returns to the composer, not the trigger (screen 06).
                    event.preventDefault();
                    const box = document.querySelector<HTMLTextAreaElement>("textarea");
                    box?.focus();
                }}
            >
                {body}
            </PopoverContent>
        </Popover>
    );
}

/** The chosen helper in the composer: removable, never a separate product. */
export function HelperChip({ helper, onRemove }: { helper: ChosenHelper; onRemove: () => void }) {
    return (
        <li
            className="flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-xs"
            data-testid="helper-chip"
        >
            <Sparkles aria-hidden className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="max-w-[12rem] truncate">{helper.name}</span>
            <button
                type="button"
                aria-label={`Remove ${helper.name}, back to Automatic`}
                className="flex h-6 w-6 items-center justify-center text-muted-foreground hover:text-foreground max-md:h-11 max-md:w-11"
                onClick={onRemove}
            >
                ×
            </button>
        </li>
    );
}

export { AUTOMATIC };
