"use client";

/**
 * Everything the Company page reads, in one round of parallel requests.
 *
 * Each source fails on its own: budgets are admin-only (a member gets a 403),
 * the task board may be off, a workspace may have no routines. A missing
 * source leaves its part of the page empty rather than the page blank, so
 * every call resolves to an empty list on any error.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
    dismissBudgetIncidentApiV1OrganizationsBudgetsIncidentsIncidentIdDismissPost,
    getWorkflowsApiV1WorkflowFetchGet,
    listAllRoutinesApiV1RoutinesGet,
    listBudgetIncidentsApiV1OrganizationsBudgetsIncidentsGet,
    listBudgetPoliciesApiV1OrganizationsBudgetsGet,
    listFoldersApiV1FolderGet,
    listTasksApiV1TasksGet,
    teamStatusApiV1TeamStatusGet,
    timelineApiV1TimelineGet,
} from "@/client/sdk.gen";
import type {
    BudgetIncidentResponse,
    BudgetPolicyResponse,
    FolderResponse,
    RoutineResponse,
    TeamMember,
    TimelineEvent,
    WorkflowListResponse,
} from "@/client/types.gen";
import type { BoardPayload, Task } from "@/components/desk/tasks";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

export type CompanyData = {
    members: TeamMember[];
    workflows: WorkflowListResponse[];
    folders: FolderResponse[];
    tasks: Task[];
    policies: BudgetPolicyResponse[];
    incidents: BudgetIncidentResponse[];
    routines: RoutineResponse[];
    events: TimelineEvent[];
};

/** A result's data, or `fallback` when the call failed or threw. */
async function settle<T>(call: Promise<{ data?: unknown; error?: unknown }>, pick: (data: unknown) => T, fallback: T): Promise<T> {
    try {
        const result = await call;
        if (result.error || result.data === undefined) return fallback;
        return pick(result.data);
    } catch {
        return fallback;
    }
}

export function useCompany() {
    const { user, loading: authLoading } = useAuth();
    const [data, setData] = useState<CompanyData | null>(null);
    const [error, setError] = useState<string | null>(null);
    const loaded = useRef(false);

    const load = useCallback(async () => {
        const [members, workflows, folders, tasks, policies, incidents, routines, events] = await Promise.all([
            settle(teamStatusApiV1TeamStatusGet({ query: { hours: 24 } }), (d) => (d as { members?: TeamMember[] }).members ?? [], [] as TeamMember[]),
            settle(
                getWorkflowsApiV1WorkflowFetchGet({ query: { status: "active" } }),
                (d) => (Array.isArray(d) ? (d as WorkflowListResponse[]) : [d as WorkflowListResponse]),
                [] as WorkflowListResponse[],
            ),
            settle(listFoldersApiV1FolderGet(), (d) => d as FolderResponse[], [] as FolderResponse[]),
            settle(listTasksApiV1TasksGet(), (d) => (d as BoardPayload).tasks ?? [], [] as Task[]),
            settle(listBudgetPoliciesApiV1OrganizationsBudgetsGet(), (d) => d as BudgetPolicyResponse[], [] as BudgetPolicyResponse[]),
            settle(listBudgetIncidentsApiV1OrganizationsBudgetsIncidentsGet(), (d) => d as BudgetIncidentResponse[], [] as BudgetIncidentResponse[]),
            settle(listAllRoutinesApiV1RoutinesGet(), (d) => (d as { routines?: RoutineResponse[] }).routines ?? [], [] as RoutineResponse[]),
            settle(timelineApiV1TimelineGet({ query: { limit: 20 } }), (d) => (d as { events?: TimelineEvent[] }).events ?? [], [] as TimelineEvent[]),
        ]);
        setData({ members, workflows, folders, tasks, policies, incidents, routines, events });
    }, []);

    useEffect(() => {
        if (authLoading || !user || loaded.current) return;
        loaded.current = true;
        void load();
        // The roster moves while the page is open; a minute is fresh enough
        // for a manager's view and cheap for the server.
        const timer = setInterval(() => void load(), 60_000);
        return () => clearInterval(timer);
    }, [authLoading, user, load]);

    const dismissIncident = useCallback(async (incidentId: number) => {
        const response = await dismissBudgetIncidentApiV1OrganizationsBudgetsIncidentsIncidentIdDismissPost({
            path: { incident_id: incidentId },
        });
        if (response.error) {
            setError(detailFromError(response.error, "Could not dismiss that alert"));
            return;
        }
        setError(null);
        setData((current) =>
            current ? { ...current, incidents: current.incidents.filter((i) => i.id !== incidentId) } : current,
        );
    }, []);

    return { data, error, dismissIncident };
}
