/**
 * The shapes the Today routes return (api/routes/today.py, services/today).
 * The routes answer with plain objects, so the generated client types them
 * loosely; these are the contract the screens read.
 */

import type { TaskState } from "@/lib/shell/taskState";

export type SectionState = "ok" | "failed";

export type ApprovalItem = {
    id: number;
    at: string | null;
    label: string;
    sentence: string;
    detail: string;
    why: string | null;
    version: string | null;
    state: string;
    workflow_id: number | null;
    thread_id: string | null;
};

export type ApprovalQueue = { count: number; items: ApprovalItem[] };

export type ScreenState =
    | "pending"
    | "approved"
    | "executing"
    | "completed"
    | "failed"
    | "outcome_unknown"
    | "cancelled";

export type ApprovalPreview = {
    id: number;
    at: string | null;
    sentence: string;
    detail: string;
    verb: string;
    action: string;
    account: string | null;
    recipient: string | null;
    amount: string | null;
    content: string | null;
    attachments: string[];
    timing: string | null;
    consequence: string;
    why: string | null;
    reversible: boolean;
    expires_at: string | null;
    version: string | null;
    revisions: { version: string; by: number; at: string }[];
    state: string;
    screen_state: ScreenState;
    error: string | null;
    done_note: string | null;
    fires_at: string | null;
    editable: boolean;
    bound_to_version: boolean;
    /** False when someone else must answer it (their consent, order or computer). */
    can_answer: boolean;
    answer_refusal: string | null;
    arguments: Record<string, unknown> | null;
    workflow_id: number | null;
    thread_id: string | null;
};

export type DueItem = {
    kind: "reminder" | "task" | "missed_call";
    id: number;
    title: string;
    at: string | null;
    when: string | null;
    overdue?: boolean;
    needs_input?: boolean;
    outcome?: string;
    href?: string;
};

export type ActiveJob = { kind: string; id: number; title: string; state: TaskState; href: string };

export type SourceView = {
    kind: string;
    label: string;
    status: "read" | "unavailable" | "failed";
    detail: string | null;
    needs_setup: boolean;
    count: number;
};

export type Suggestion = {
    key: string;
    title: string;
    why: string;
    action:
        | { kind: "propose_callback"; missed_call_id: number }
        | { kind: "open_reminder"; reminder_id: number }
        | { kind: "open_brief_settings" }
        | { kind: "none" };
};

export type BriefView = {
    id: number;
    kind: "brief" | "end_of_day";
    date: string;
    timezone: string;
    period_start: string;
    period_end: string;
    covered: string;
    refreshed_at: string;
    status: "complete" | "partial" | "failed";
    summary: string;
    sources: SourceView[];
    sections: Record<string, unknown>;
    delivered_at: string | null;
    stale?: boolean;
};

export type UpcomingEvent = {
    kind: "event";
    id: number;
    title: string;
    at: string;
    when: string;
    timezone: string;
    reminders: { id: number; offset_minutes: number | null; status: string }[];
    revision: number;
};

export type TodayView = {
    date: string;
    date_label: string;
    timezone: string;
    refreshed_at: string;
    organization_id: number;
    order: string[];
    sections: {
        approvals: ({ state: SectionState; message?: string } & Partial<ApprovalQueue>);
        due: { state: SectionState; message?: string; items: DueItem[]; active?: ActiveJob[] };
        brief: null | {
            state: SectionState;
            message?: string;
            item: BriefView | null;
            enabled: boolean;
            paused: boolean;
            next_sentence: string;
        };
        upcoming: null | { state: SectionState; message?: string; items: UpcomingEvent[] };
        end_of_day: null | { state: SectionState; message?: string; item: BriefView | null };
        suggestions: { state: SectionState; items: Suggestion[] };
    };
    missing_sources: { kind: string; message: string }[];
    empty: boolean;
    empty_copy: string | null;
};

export type ActivityItem = {
    kind: "card" | "task" | "delivery";
    id: number;
    title: string;
    state: TaskState;
    at: string | null;
    helper: string;
    evidence: string | null;
};

export type ActivityDetail = {
    kind: "card" | "task" | "delivery";
    id: number;
    goal: string;
    brief?: string;
    owner: string;
    scope: string;
    state: TaskState;
    version?: string | number | null;
    due?: string | null;
    stages: { label: string; at: string | null; reason_code?: string | null }[];
    inputs: Record<string, unknown>;
    evidence: string | null;
    related: { kind: string; id: number; href: string; title?: string }[];
    can_cancel: boolean;
    can_retry: boolean;
    check_delivery: boolean;
};

export type Channel = "in_app" | "whatsapp" | "push";

export type ChannelState = {
    channel: Channel;
    state: "available" | "needs_setup" | "unavailable";
    reason: string | null;
};

export type ReminderView = {
    id: number;
    title: string;
    note: string;
    event_id: number | null;
    offset_minutes: number | null;
    offset_words: string | null;
    recurrence: "once" | "daily" | "weekdays" | "weekly";
    local_time: string | null;
    weekday: number | null;
    timezone: string;
    remind_at: string | null;
    when: string | null;
    channel: Channel;
    status: "active" | "paused" | "done" | "missed" | "cancelled";
    revision: number;
    history: { at: string; why: string; old: string | null; new: string }[];
    last_delivered_at: string | null;
    deliveries?: DeliveryView[];
};

export type EventView = {
    id: number;
    title: string;
    starts_at: string;
    timezone: string;
    when: string;
    status: string;
    revision: number;
};

export type DeliveryView = {
    id: number;
    channel: Channel;
    status: string;
    reason_code: string | null;
    detail: string | null;
    evidence: string | null;
    is_test: boolean;
    occurrence_key: string;
    duplicate?: boolean;
};

export type ReminderPreview = {
    next_at: string | null;
    sentence: string;
    schedule_key: string;
    timezone: string;
    problems: { code: "invalid_past" | "timezone_conflict"; message: string }[];
    event: EventView | null;
};

export type BriefSettings = {
    enabled: boolean;
    paused: boolean;
    local_time: string;
    timezone: string;
    days: number[];
    channels: Channel[];
    quiet_start: string;
    quiet_end: string;
    end_of_day_enabled: boolean;
    end_of_day_time: string;
    revision: number;
    saved: boolean;
    channel_states: ChannelState[];
    next_at: string | null;
    next_sentence: string;
    end_of_day_next: string | null;
    sources: { kind: string; label: string }[];
};

export const CHANNEL_LABEL: Record<Channel, string> = {
    in_app: "In the app",
    whatsapp: "WhatsApp",
    push: "Phone notification",
};

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
