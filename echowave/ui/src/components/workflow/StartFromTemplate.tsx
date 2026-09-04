"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { detailFromResult } from "@/lib/apiError";
import logger from "@/lib/logger";

/**
 * The six ready-made agents, offered where an account has none.
 *
 * The catalogue has existed in the API since before this screen did, and
 * nothing rendered it — a new account met "No active workflows found" and a
 * button to the wizard, which asks eleven questions and then runs a language
 * model to write a flow. That is the right door for a business we have no
 * template for. It is the wrong one for a dental clinic when a dental clinic
 * template already exists, is better than anything written on the spot, and
 * opens in about a second.
 *
 * One click creates the agent and opens it. There is no preview step: the
 * agent it makes is editable, discardable, and faster to read than any summary
 * of it would be.
 */

type TemplateCard = {
    id: string;
    name: string;
    vertical: string;
    direction: string;
    summary: string;
    languages: string[];
};

export function StartFromTemplate() {
    const router = useRouter();
    const [templates, setTemplates] = useState<TemplateCard[] | null>(null);
    const [creating, setCreating] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        void (async () => {
            const response = await client.get({ url: "/api/v1/agent-templates" });
            if (cancelled || response.error) {
                // Silent: templates are an offer, not the page. If they cannot
                // be fetched the create button below still works.
                setTemplates([]);
                return;
            }
            const data = response.data as { templates?: TemplateCard[] } | undefined;
            setTemplates(data?.templates ?? []);
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const start = async (template: TemplateCard) => {
        setCreating(template.id);
        setError(null);
        const response = await client.post({
            url: `/api/v1/agent-templates/${template.id}/create`,
        });
        if (response.error) {
            setCreating(null);
            const message = detailFromResult(response, "Could not start from that template.");
            logger.error(`Template create failed: ${message}`);
            setError(message);
            return;
        }
        const created = response.data as { id?: number } | undefined;
        if (created?.id == null) {
            setCreating(null);
            setError("The agent was created but we could not open it. It is in your list.");
            return;
        }
        router.push(`/workflow/${created.id}`);
    };

    if (templates === null || templates.length === 0) return null;

    return (
        <div className="mt-4">
            <h3 className="mb-1 text-sm font-medium">Start from a ready-made agent</h3>
            <p className="mb-3 text-sm text-muted-foreground">
                Each one is a working agent for that business. Open it, change what you
                need, and put it on a number.
            </p>

            {error && (
                <p className="mb-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                    {error}
                </p>
            )}

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {templates.map((template) => (
                    <button
                        key={template.id}
                        type="button"
                        disabled={creating !== null}
                        onClick={() => void start(template)}
                        className="rounded-lg border border-border p-4 text-left transition-colors hover:bg-muted/40 disabled:cursor-wait disabled:opacity-60"
                    >
                        <span className="flex items-center justify-between gap-2">
                            <span className="font-medium">{template.name}</span>
                            {creating === template.id && (
                                <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                            )}
                        </span>
                        <span className="mt-1 block text-xs text-muted-foreground">
                            {template.summary}
                        </span>
                        <span className="mt-2 block text-xs text-muted-foreground">
                            {template.direction === "inbound" ? "Answers calls" : "Makes calls"}
                            {template.languages.length > 0 &&
                                ` · ${template.languages.length} languages`}
                        </span>
                    </button>
                ))}
            </div>
        </div>
    );
}
