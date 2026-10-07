/**
 * Help (screen 28): a person's own support requests, through the generated
 * client. Each call returns either the value or a sentence saying why not,
 * so a screen never shows a fake success or a blank where an error was.
 */

import {
    attachApiV1HelpTicketsTicketIdAttachmentsPost,
    createTicketApiV1HelpTicketsPost,
    helpOptionsApiV1HelpOptionsGet,
    myTicketApiV1HelpTicketsTicketIdGet,
    myTicketsApiV1HelpTicketsGet,
    reopenApiV1HelpTicketsTicketIdReopenPost,
    replyApiV1HelpTicketsTicketIdMessagesPost,
    resolveApiV1HelpTicketsTicketIdResolvePost,
    sharePreviewApiV1HelpSharePreviewPost,
} from "@/client/sdk.gen";
import type {
    HelpOptions,
    SharePreview,
    TicketAttachment,
    TicketDetail,
    TicketMessage,
    TicketSummary,
} from "@/client/types.gen";
import { useAppConfig } from "@/context/AppConfigContext";
import { useOrgConfig } from "@/context/OrgConfigContext";
import { detailFromResult } from "@/lib/apiError";

export type { HelpOptions, SharePreview, TicketAttachment, TicketDetail, TicketMessage, TicketSummary };

export type Outcome<T> = { ok: true; value: T } | { ok: false; error: string; status?: number };

export type AffectedKind = "task" | "reply";

type Result = { data?: unknown; error?: unknown; response?: { status?: number } | null };

function settle<T>(result: Result, fallback: string): Outcome<T> {
    if (result.error || result.data === undefined) {
        return { ok: false, error: detailFromResult(result, fallback), status: result.response?.status };
    }
    return { ok: true, value: result.data as T };
}

/** A fresh key for one submit or one message: a retry reuses it. */
export function newKey(): string {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Whether Help is on for this workspace, distinguishing "not known yet". */
export function useHelpAvailability(): "loading" | "on" | "off" {
    const { config, loading } = useAppConfig();
    const org = useOrgConfig();
    if (config?.features?.support_help || org.orgFeatures?.support_help) return "on";
    if (loading || org.loading) return "loading";
    return "off";
}

export const STATUS_LABEL: Record<string, string> = {
    open: "Open",
    waiting_on_customer: "Waiting for you",
    in_progress: "In progress",
    resolved: "Resolved",
};

export const ACTION_STATE_LABEL: Record<string, string> = {
    requested: "Waiting for approval",
    approved: "Approved, not started",
    queued: "Queued",
    running: "Running",
    succeeded: "Done",
    failed: "Not done",
    outcome_unknown: "Being checked",
    rejected: "Not going ahead",
    expired: "Not going ahead",
    cancelled: "Not going ahead",
};

export async function loadOptions(): Promise<Outcome<HelpOptions>> {
    return settle(await helpOptionsApiV1HelpOptionsGet(), "Help could not load.");
}

export async function previewShare(input: {
    affectedKind: AffectedKind | null;
    affectedId: number | null;
    share: string[] | null;
}): Promise<Outcome<SharePreview>> {
    return settle(
        await sharePreviewApiV1HelpSharePreviewPost({
            body: {
                affected_kind: input.affectedKind,
                affected_id: input.affectedId,
                share: input.share,
            },
        }),
        "Could not show what will be shared.",
    );
}

export async function createTicket(input: {
    category: string;
    description: string;
    affectedKind: AffectedKind | null;
    affectedId: number | null;
    share: string[] | null;
    key: string;
}): Promise<Outcome<{ ticket: TicketSummary; created: boolean }>> {
    return settle(
        await createTicketApiV1HelpTicketsPost({
            body: {
                category: input.category,
                description: input.description,
                affected_kind: input.affectedKind,
                affected_id: input.affectedId,
                share: input.share,
            },
            headers: { "Idempotency-Key": input.key },
        }),
        "Your request was not sent. Try again.",
    );
}

export async function listTickets(): Promise<Outcome<TicketSummary[]>> {
    return settle(await myTicketsApiV1HelpTicketsGet(), "Your requests could not load.");
}

export async function loadTicket(id: number): Promise<Outcome<TicketDetail>> {
    return settle(await myTicketApiV1HelpTicketsTicketIdGet({ path: { ticket_id: id } }), "This request could not load.");
}

export async function sendReply(id: number, body: string, key: string): Promise<Outcome<TicketMessage>> {
    return settle(
        await replyApiV1HelpTicketsTicketIdMessagesPost({ path: { ticket_id: id }, body: { body, client_key: key } }),
        "Your message was not sent. Try again.",
    );
}

export async function resolveTicket(id: number): Promise<Outcome<TicketDetail>> {
    return settle(await resolveApiV1HelpTicketsTicketIdResolvePost({ path: { ticket_id: id } }), "Could not mark it resolved.");
}

export async function reopenTicket(id: number, body: string | null): Promise<Outcome<TicketDetail>> {
    return settle(
        await reopenApiV1HelpTicketsTicketIdReopenPost({ path: { ticket_id: id }, body: { body } }),
        "Could not reopen it.",
    );
}

export async function attachFile(id: number, file: File): Promise<Outcome<TicketAttachment>> {
    return settle(
        await attachApiV1HelpTicketsTicketIdAttachmentsPost({ path: { ticket_id: id }, body: { file } }),
        "The file was not added.",
    );
}
