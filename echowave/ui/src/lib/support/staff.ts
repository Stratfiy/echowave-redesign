/**
 * The staff support console (screens 32 and 33): the shapes the
 * `/admin/support` routes return, and the calls the screens make. The
 * routes return plain dicts, so the generated client types them loosely;
 * the shapes are declared once here rather than cast in each screen.
 */

import {
    attachmentLinkApiV1AdminSupportAttachmentsAttachmentIdLinkGet,
    supportActionApiV1AdminSupportActionsActionIdGet,
    supportActionApproveApiV1AdminSupportActionsActionIdApprovePost,
    supportActionListApiV1AdminSupportActionsGet,
    supportActionPreviewApiV1AdminSupportActionsPreviewPost,
    supportActionReconcileApiV1AdminSupportActionsActionIdReconcilePost,
    supportActionRejectApiV1AdminSupportActionsActionIdRejectPost,
    supportActionRequestApiV1AdminSupportActionsPost,
    supportActionRunApiV1AdminSupportActionsActionIdRunPost,
    supportActionWithdrawApiV1AdminSupportActionsActionIdWithdrawPost,
    supportCaseApiV1AdminSupportTicketsTicketIdGet,
    supportCommandsApiV1AdminSupportActionsCommandsGet,
    supportNoteApiV1AdminSupportTicketsTicketIdNotesPost,
    supportQueueApiV1AdminSupportTicketsGet,
    supportReplyApiV1AdminSupportTicketsTicketIdRepliesPost,
    supportStaffApiV1AdminSupportStaffGet,
    supportUpdateApiV1AdminSupportTicketsTicketIdPatch,
} from "@/client/sdk.gen";
import { detailFromResult } from "@/lib/apiError";

export type Outcome<T> = { ok: true; value: T } | { ok: false; error: string; status?: number; detail?: unknown };

type Result = { data?: unknown; error?: unknown; response?: { status?: number } | null };

function settle<T>(result: Result, fallback: string, pick?: (data: unknown) => T): Outcome<T> {
    if (result.error || result.data === undefined) {
        const raw = (result.error as { detail?: unknown } | undefined)?.detail;
        const message =
            raw && typeof raw === "object" && "message" in (raw as Record<string, unknown>)
                ? String((raw as { message: unknown }).message)
                : detailFromResult(result, fallback);
        return { ok: false, error: message, status: result.response?.status, detail: raw };
    }
    return { ok: true, value: pick ? pick(result.data) : (result.data as T) };
}

export interface QueueRow {
    id: number;
    subject: string;
    category: string;
    category_label: string;
    status: string;
    severity: string;
    assignee_user_id: number | null;
    requester_email: string | null;
    workspace_name: string | null;
    organization_id: number;
    requester_user_id: number;
    created_at: string | null;
    updated_at: string | null;
    overdue: boolean;
    next_step: string;
    linked_incident: string | null;
    version: number;
}

export interface ShareField {
    label: string;
    value: unknown;
}

export interface SharedSnapshot {
    affected: { kind: string; id: number } | null;
    sections: { key: string; label: string; fields: ShareField[] }[];
    left_out: string[];
    shared_at: string | null;
}

export interface CaseMessage {
    id: number;
    author_kind: "customer" | "staff" | "system";
    body: string;
    created_at: string | null;
}

export interface CaseNote {
    id: number;
    author: string;
    body: string;
    created_at: string | null;
}

export interface CaseHistory {
    id: number;
    at: string | null;
    actor: string;
    action: string;
    note: string;
}

export interface SupportCase extends QueueRow {
    shared: SharedSnapshot;
    messages: CaseMessage[];
    attachments: { id: number; file_name: string; content_type: string; size_bytes: number; created_at: string | null }[];
    notes: CaseNote[];
    history: CaseHistory[];
    requester: { id: number; email: string | null };
    workspace: { id: number; name: string | null };
    first_response_at: string | null;
    resolved_at: string | null;
    reopened_count: number;
    affected: { kind: string; id: number } | null;
    diagnostics: {
        task: { id?: number; state: string; version?: number; history?: { to: string; at: string; reason_code: string | null }[]; note?: string } | null;
        allowances: { kind: string; unit: string; used: number; limit: number; remaining: number }[] | null;
    };
}

export interface StaffMember {
    id: number;
    email: string | null;
    role: string;
}

export interface CommandField {
    name: string;
    label: string;
    type: "choice" | "integer" | "text";
    choices?: string[];
    min?: number;
    max?: number;
}

export interface CommandRow {
    kind: string;
    title: string;
    description: string;
    approver_role: string;
    needs_customer_request: boolean;
    state: "available" | "needs_setup" | "unavailable";
    reason: string | null;
    fields: CommandField[];
}

export interface ActionPreviewShape {
    title: string;
    changes: { field: string; old: unknown; new: unknown }[];
    impact: string | null;
    dependencies: string[];
    customer_summary: string;
    target: { organization_id: number; user_id: number | null; ticket_id: number | null };
    environment: string;
    approvers: string;
    version: string;
    params?: Record<string, unknown>;
}

export type ActionState =
    | "requested"
    | "approved"
    | "rejected"
    | "expired"
    | "cancelled"
    | "queued"
    | "running"
    | "succeeded"
    | "failed"
    | "outcome_unknown";

export interface SupportAction {
    id: number;
    ticket_id: number | null;
    organization_id: number;
    target_user_id: number | null;
    kind: string;
    title: string;
    params: Record<string, unknown>;
    preview: Omit<ActionPreviewShape, "version" | "params">;
    version: string;
    reason: string;
    state: ActionState;
    environment: string;
    approver_role: string | null;
    requested_by: string | null;
    requested_by_id: number;
    approved_by: string | null;
    approved_by_id: number | null;
    approved_version: string | null;
    approved_at: string | null;
    decided_note: string | null;
    run_by: string | null;
    queued_at: string | null;
    started_at: string | null;
    finished_at: string | null;
    result: { summary?: string; reason_code?: string; evidence?: Record<string, unknown> } | null;
    idempotency_key: string;
    expires_at: string | null;
    created_at: string | null;
    notice: string | null;
    history?: { id: number; at: string | null; actor: string; action: string }[];
}

export const SEVERITIES = ["low", "normal", "high", "urgent"] as const;
export const CASE_STATUSES = ["open", "waiting_on_customer", "in_progress", "resolved"] as const;

export const CASE_STATUS_LABEL: Record<string, string> = {
    open: "Open",
    waiting_on_customer: "Waiting on customer",
    in_progress: "In progress",
    resolved: "Resolved",
};

export const ACTION_STATE_LABEL: Record<ActionState, string> = {
    requested: "Approval required",
    approved: "Approved, not run",
    rejected: "Rejected",
    expired: "Expired",
    cancelled: "Withdrawn",
    queued: "Queued",
    running: "Running",
    succeeded: "Succeeded",
    failed: "Failed",
    outcome_unknown: "Outcome unknown",
};

export async function loadQueue(filters: {
    status?: string;
    assignee?: string;
    severity?: string;
    overdue?: boolean;
}): Promise<Outcome<QueueRow[]>> {
    return settle(
        await supportQueueApiV1AdminSupportTicketsGet({
            query: {
                status: filters.status || "active",
                assignee: filters.assignee || undefined,
                severity: filters.severity || undefined,
                overdue: filters.overdue || undefined,
            },
        }),
        "The queue could not load.",
        (data) => ((data as { tickets?: QueueRow[] }).tickets ?? []),
    );
}

export async function loadCase(id: number): Promise<Outcome<SupportCase>> {
    return settle(await supportCaseApiV1AdminSupportTicketsTicketIdGet({ path: { ticket_id: id } }), "This case could not load.");
}

export async function replyToCase(id: number, body: string, key: string, thenStatus: string | null): Promise<Outcome<CaseMessage>> {
    return settle(
        await supportReplyApiV1AdminSupportTicketsTicketIdRepliesPost({
            path: { ticket_id: id },
            body: { body, client_key: key, then_status: thenStatus },
        }),
        "The reply was not sent. Your text is kept; try again.",
    );
}

export async function noteOnCase(id: number, body: string, key: string): Promise<Outcome<{ id: number }>> {
    return settle(
        await supportNoteApiV1AdminSupportTicketsTicketIdNotesPost({ path: { ticket_id: id }, body: { body, client_key: key } }),
        "The note was not saved. Your text is kept; try again.",
    );
}

export async function updateCase(
    id: number,
    expectedVersion: number,
    changes: Partial<Pick<QueueRow, "assignee_user_id" | "severity" | "status" | "linked_incident">>,
): Promise<Outcome<QueueRow>> {
    return settle(
        await supportUpdateApiV1AdminSupportTicketsTicketIdPatch({
            path: { ticket_id: id },
            body: { expected_version: expectedVersion, ...changes },
        }),
        "The case was not changed.",
    );
}

export async function loadStaff(): Promise<Outcome<StaffMember[]>> {
    return settle(await supportStaffApiV1AdminSupportStaffGet(), "Staff could not load.", (data) => ((data as { staff?: StaffMember[] }).staff ?? []));
}

export async function attachmentLink(id: number): Promise<Outcome<string>> {
    return settle(
        await attachmentLinkApiV1AdminSupportAttachmentsAttachmentIdLinkGet({ path: { attachment_id: id } }),
        "The file could not be opened.",
        (data) => String((data as { url?: string }).url ?? ""),
    );
}

export async function loadCommands(organizationId: number | null): Promise<Outcome<CommandRow[]>> {
    return settle(
        await supportCommandsApiV1AdminSupportActionsCommandsGet({ query: { organization_id: organizationId ?? undefined } }),
        "The commands could not load.",
        (data) => ((data as { commands?: CommandRow[] }).commands ?? []),
    );
}

export interface ActionTarget {
    kind: string;
    organization_id: number;
    target_user_id: number | null;
    ticket_id: number | null;
    params: Record<string, unknown>;
}

export async function previewAction(target: ActionTarget): Promise<Outcome<ActionPreviewShape>> {
    return settle(await supportActionPreviewApiV1AdminSupportActionsPreviewPost({ body: target }), "The preview could not be made.");
}

export async function requestAction(
    target: ActionTarget,
    reason: string,
    expectedVersion: string,
    key: string,
): Promise<Outcome<{ action: SupportAction; created: boolean }>> {
    return settle(
        await supportActionRequestApiV1AdminSupportActionsPost({
            body: { ...target, reason, expected_version: expectedVersion },
            headers: { "Idempotency-Key": key },
        }),
        "The request was not recorded.",
    );
}

export async function listActions(filters: { state?: string; ticketId?: number }): Promise<Outcome<SupportAction[]>> {
    return settle(
        await supportActionListApiV1AdminSupportActionsGet({
            query: { state: filters.state || undefined, ticket_id: filters.ticketId },
        }),
        "Actions could not load.",
        (data) => ((data as { actions?: SupportAction[] }).actions ?? []),
    );
}

export async function loadAction(id: number): Promise<Outcome<SupportAction>> {
    return settle(await supportActionApiV1AdminSupportActionsActionIdGet({ path: { action_id: id } }), "This action could not load.");
}

export async function approveAction(id: number, version: string): Promise<Outcome<SupportAction>> {
    return settle(
        await supportActionApproveApiV1AdminSupportActionsActionIdApprovePost({ path: { action_id: id }, body: { version } }),
        "Not approved.",
    );
}

export async function rejectAction(id: number, note: string): Promise<Outcome<SupportAction>> {
    return settle(
        await supportActionRejectApiV1AdminSupportActionsActionIdRejectPost({ path: { action_id: id }, body: { note } }),
        "Not rejected.",
    );
}

export async function withdrawAction(id: number): Promise<Outcome<SupportAction>> {
    return settle(await supportActionWithdrawApiV1AdminSupportActionsActionIdWithdrawPost({ path: { action_id: id } }), "Not withdrawn.");
}

export async function runAction(id: number): Promise<Outcome<{ action: SupportAction; queued: boolean }>> {
    return settle(await supportActionRunApiV1AdminSupportActionsActionIdRunPost({ path: { action_id: id } }), "Not started.");
}

export async function reconcileAction(id: number): Promise<Outcome<SupportAction>> {
    return settle(
        await supportActionReconcileApiV1AdminSupportActionsActionIdReconcilePost({ path: { action_id: id } }),
        "Could not reconcile yet.",
    );
}
