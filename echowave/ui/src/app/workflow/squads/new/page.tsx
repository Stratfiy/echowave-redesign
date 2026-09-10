"use client";

/**
 * A squad, from a form.
 *
 * The engine has had squads for a while — a `handoff` node names another
 * agent, and at call time that agent's conversation is spliced in — but the
 * only way to make one was to know that, open the canvas, find the node in
 * the palette and wire it. Vapi puts "Create squad" next to "Create
 * assistant"; Bolna has agent groups. This is that door.
 *
 * What it builds is deliberately plain: a front-desk step that greets and
 * works out what the caller wants, and one handoff per member with the
 * condition that sends the call there. Everything it makes is an ordinary
 * agent afterwards — editable on the canvas, testable, deployable — so the
 * form does not need to cover every case, only the common one.
 */

import { ArrowLeft, ArrowRightLeft, Loader2, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import posthog from "posthog-js";
import { useEffect, useRef, useState } from "react";

import {
    createWorkflowApiV1WorkflowCreateDefinitionPost,
    getWorkflowsSummaryApiV1WorkflowSummaryGet,
} from "@/client/sdk.gen";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { PostHogEvent } from "@/constants/posthog-events";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import logger from "@/lib/logger";

import { buildSquadDefinition } from "../buildSquad";

type Agent = { id: number; name: string; uuid: string };
type Member = { agentUuid: string; when: string };

const DEFAULT_GREETING = "Hello, thanks for calling. How can I help you today?";
const DEFAULT_PROMPT =
    "You are the front desk. Greet the caller, find out in one or two questions " +
    "what they need, and hand the call to the right specialist. Do not try to " +
    "resolve the request yourself. Follow the caller's language.";

export default function NewSquadPage() {
    const router = useRouter();
    const { user, loading: authLoading } = useAuth();
    const [agents, setAgents] = useState<Agent[] | null>(null);
    const [name, setName] = useState("");
    const [greeting, setGreeting] = useState(DEFAULT_GREETING);
    const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
    const [persona, setPersona] = useState(
        "Warm and brief. One question per turn. Follow the caller's language and switch if they do.",
    );
    // Arrived from an agent's "Add to a squad": that agent is the first member.
    const preselected = useSearchParams().get("agent") ?? "";
    const [members, setMembers] = useState<Member[]>([{ agentUuid: preselected, when: "" }]);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const hasFetched = useRef(false);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void (async () => {
            const response = await getWorkflowsSummaryApiV1WorkflowSummaryGet({
                query: { status: "active" },
            });
            if (response.error) {
                setError(detailFromResult(response, "Could not load your agents."));
                setAgents([]);
                return;
            }
            // `workflow_uuid` is on the wire but not on the generated type yet.
            const rows = (response.data ?? []) as Array<{
                id: number;
                name: string;
                workflow_uuid?: string | null;
            }>;
            setAgents(
                rows
                    .filter((row) => row.workflow_uuid)
                    .map((row) => ({ id: row.id, name: row.name, uuid: row.workflow_uuid as string })),
            );
        })();
    }, [authLoading, user]);

    const setMember = (index: number, patch: Partial<Member>) =>
        setMembers((prev) => prev.map((m, i) => (i === index ? { ...m, ...patch } : m)));

    const chosen = members.filter((m) => m.agentUuid && m.when.trim());
    const canCreate = name.trim().length > 0 && chosen.length > 0 && !busy;

    const create = async () => {
        if (!canCreate) return;
        setBusy(true);
        setError(null);
        const definition = buildSquadDefinition({
            greeting: greeting.trim(),
            prompt: prompt.trim(),
            persona: persona.trim(),
            members: chosen.map((m) => ({
                agentUuid: m.agentUuid,
                name: agents?.find((a) => a.uuid === m.agentUuid)?.name ?? "Specialist",
                when: m.when.trim(),
            })),
        });
        const response = await createWorkflowApiV1WorkflowCreateDefinitionPost({
            body: { name: name.trim(), workflow_definition: definition },
        });
        setBusy(false);
        if (response.error || !response.data?.id) {
            const message = detailFromResult(response, "Could not create the squad.");
            logger.error(`Squad create failed: ${message}`);
            posthog.capture(PostHogEvent.SQUAD_CREATE_FAILED, { members: chosen.length, reason: message });
            setError(message);
            return;
        }
        posthog.capture(PostHogEvent.SQUAD_CREATED, {
            workflow_id: response.data.id,
            members: chosen.length,
        });
        router.push(`/workflow/${response.data.id}`);
    };

    return (
        <>
            <PageHeader
                title="New squad"
                description="One front desk that greets the caller and hands the call to the right agent. Each member is an agent you already have."
                actions={
                    <Button asChild variant="ghost" size="sm">
                        <Link href="/workflow">
                            <ArrowLeft className="h-4 w-4" />
                            Agents
                        </Link>
                    </Button>
                }
            />
            <PageBody>
                <form
                    className="mx-auto max-w-3xl space-y-8"
                    onSubmit={(event) => {
                        event.preventDefault();
                        void create();
                    }}
                >
                    <section className="space-y-4 rounded-[var(--radius-large)] border border-border bg-card p-6">
                        <div className="space-y-2">
                            <Label htmlFor="squad-name">Squad name</Label>
                            <Input
                                id="squad-name"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                placeholder="Main line"
                                maxLength={120}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="squad-greeting">First message</Label>
                            <Textarea
                                id="squad-greeting"
                                rows={2}
                                value={greeting}
                                onChange={(e) => setGreeting(e.target.value)}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="squad-prompt">Front desk instruction</Label>
                            <Textarea
                                id="squad-prompt"
                                rows={4}
                                value={prompt}
                                onChange={(e) => setPrompt(e.target.value)}
                                className="font-mono text-sm leading-relaxed"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="squad-persona">Rules and persona</Label>
                            <Textarea
                                id="squad-persona"
                                rows={2}
                                value={persona}
                                onChange={(e) => setPersona(e.target.value)}
                            />
                        </div>
                    </section>

                    <section className="space-y-4 rounded-[var(--radius-large)] border border-border bg-card p-6">
                        <div>
                            <h2 className="font-medium">Members</h2>
                            <p className="text-sm text-muted-foreground">
                                Which agent takes the call, and when. The call continues with that
                                agent and does not come back.
                            </p>
                        </div>

                        {agents !== null && agents.length === 0 && (
                            <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
                                A squad is made of agents you already have. Build one first, then
                                come back.
                            </p>
                        )}

                        <ol className="space-y-3">
                            {members.map((member, index) => (
                                <li key={index} className="grid gap-3 rounded-xl border border-border p-4 sm:grid-cols-[1fr_1.4fr_auto]">
                                    <div className="space-y-2">
                                        <Label htmlFor={`squad-member-${index}`}>Agent</Label>
                                        <select
                                            id={`squad-member-${index}`}
                                            value={member.agentUuid}
                                            onChange={(e) => setMember(index, { agentUuid: e.target.value })}
                                            className="h-9 w-full rounded-[var(--radius-control)] border border-input bg-card px-3 text-sm"
                                        >
                                            <option value="">Choose an agent</option>
                                            {(agents ?? []).map((agent) => (
                                                <option key={agent.uuid} value={agent.uuid}>
                                                    {agent.name}
                                                </option>
                                            ))}
                                        </select>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor={`squad-when-${index}`}>Hand over when</Label>
                                        <Input
                                            id={`squad-when-${index}`}
                                            value={member.when}
                                            onChange={(e) => setMember(index, { when: e.target.value })}
                                            placeholder="The caller wants to book or change an appointment"
                                        />
                                    </div>
                                    <div className="flex items-end">
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            aria-label="Remove member"
                                            disabled={members.length === 1}
                                            onClick={() => setMembers((prev) => prev.filter((_, i) => i !== index))}
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>
                                </li>
                            ))}
                        </ol>
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => setMembers((prev) => [...prev, { agentUuid: "", when: "" }])}
                        >
                            <Plus className="h-4 w-4" />
                            Add member
                        </Button>
                    </section>

                    {error && (
                        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                            {error}
                        </p>
                    )}

                    <div className="flex items-center justify-end gap-3">
                        <Button type="submit" disabled={!canCreate}>
                            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRightLeft className="h-4 w-4" />}
                            Create squad
                        </Button>
                    </div>
                </form>
            </PageBody>
        </>
    );
}
