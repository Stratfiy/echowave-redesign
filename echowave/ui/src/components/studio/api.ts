/**
 * Studio's calls to the api (api/routes/studio.py).
 *
 * Through the shared client with explicit URLs, like the agent builder's
 * panel: the generated SDK types responses as a status->shape map, and these
 * screens only ever read the success shape, so each call asserts it. Every
 * call returns `{ data }` or `{ error }` and never throws for a 4xx/5xx --
 * the same contract as the generated client (ui/AGENTS.md).
 */

import { client } from "@/client/client.gen";
import { detailFromError } from "@/lib/apiError";

export interface StudioUsage {
    used: number;
    /** The plan's monthly allowance; 0 is unlimited. */
    limit: number;
    remaining: number;
    past_allowance?: boolean;
    per_message_credits?: number;
    charged_credits?: number;
}

export interface StudioConfig {
    available: boolean;
    unavailable_reason: string | null;
    builds_configured: boolean;
    usage: StudioUsage & { resets_at?: string };
}

export interface StudioChatResponse {
    reply: string;
    history: Record<string, unknown>[];
    actions: string[];
    created_workflow_ids: number[];
    site_id: number | null;
    usage: StudioUsage;
}

export interface SiteFile {
    path: string;
    bytes: number;
}

export interface Site {
    id: number;
    name: string;
    framework: string;
    build_status: "none" | "building" | "succeeded" | "failed";
    built_at: string | null;
    build_seconds: number | null;
    agent_workflow_ids: number[];
    updated_at: string | null;
    preview_url: string | null;
    files?: SiteFile[];
    build_log?: string | null;
}

export interface BuildResult {
    status: "succeeded" | "failed";
    seconds: number | null;
    preview_url: string | null;
    errors?: string;
    build_log?: string | null;
}

type Result<T> = { data: T; error?: undefined } | { data?: undefined; error: string };

async function call<T>(
    request: Promise<{ data?: unknown; error?: unknown }>,
    fallback: string,
): Promise<Result<T>> {
    try {
        const response = await request;
        if (response.error) return { error: detailFromError(response.error, fallback) };
        if (response.data === undefined) return { error: fallback };
        return { data: response.data as T };
    } catch (err) {
        return { error: detailFromError(err, fallback) };
    }
}

export const studioApi = {
    config: () =>
        call<StudioConfig>(
            client.get({ url: "/api/v1/studio/config" }),
            "Could not load Studio.",
        ),
    chat: (message: string, history: Record<string, unknown>[]) =>
        call<StudioChatResponse>(
            client.post({ url: "/api/v1/studio/chat", body: { message, history } }),
            "Studio could not reply.",
        ),
    listSites: () =>
        call<{ sites: Site[] }>(
            client.get({ url: "/api/v1/studio/sites" }),
            "Could not load your sites.",
        ),
    getSite: (siteId: number) =>
        call<Site>(
            client.get({ url: `/api/v1/studio/sites/${siteId}` }),
            "Could not load the site.",
        ),
    readFile: (siteId: number, path: string) =>
        call<{ path: string; content: string }>(
            client.get({
                url: `/api/v1/studio/sites/${siteId}/file`,
                query: { path },
            }),
            "Could not read the file.",
        ),
    build: (siteId: number) =>
        call<BuildResult>(
            client.post({ url: `/api/v1/studio/sites/${siteId}/build` }),
            "The build could not start.",
        ),
    download: (siteId: number) =>
        call<Blob>(
            client.get({
                url: `/api/v1/studio/sites/${siteId}/download`,
                parseAs: "blob",
            }),
            "Could not download the site.",
        ),
    deleteSite: (siteId: number) =>
        call<{ deleted: boolean }>(
            client.delete({ url: `/api/v1/studio/sites/${siteId}` }),
            "Could not delete the site.",
        ),
};
