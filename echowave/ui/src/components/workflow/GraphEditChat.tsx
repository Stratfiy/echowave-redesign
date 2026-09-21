"use client";

import { useEffect, useRef, useState } from "react";

import { graphSnapshot, useUndoRedo, useWorkflowStore } from "@/app/workflow/[workflowId]/stores/workflowStore";
import { client } from "@/client/client.gen";
import { FlowEdge, FlowNode } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

interface Proposal {
    status: "proposal" | "clarification";
    summary: string;
    graph: { nodes: FlowNode[]; edges: FlowEdge[] } | null;
    changes: { node_id: string; field: string; before: string; after: string }[];
}

/** Proposes edits against the exact visible draft; applying never saves or publishes. */
export function GraphEditChat({ workflowId, readOnly = false }: { workflowId: number; readOnly?: boolean }) {
    const { user, loading } = useAuth();
    const nodes = useWorkflowStore((state) => state.nodes);
    const edges = useWorkflowStore((state) => state.edges);
    const loadedId = useWorkflowStore((state) => state.workflowId);
    const ready = loadedId === workflowId;
    const applyGraphProposal = useWorkflowStore((state) => state.applyGraphProposal);
    const { undo, redo, canUndo, canRedo } = useUndoRedo();
    const [message, setMessage] = useState("");
    const [request, setRequest] = useState("");
    const [proposal, setProposal] = useState<Proposal | null>(null);
    const [base, setBase] = useState("");
    const [sending, setSending] = useState(false);
    const [notice, setNotice] = useState("");
    const [error, setError] = useState("");
    const generation = useRef(0);
    const [clarificationContext, setClarificationContext] = useState("");
    useEffect(() => () => { generation.current += 1; }, []);
    const stale = Boolean(proposal?.graph && base !== graphSnapshot(nodes, edges));

    async function propose() {
        if (loading || !user || readOnly || !ready || sending || !message.trim()) return;
        const token = ++generation.current;
        const current = useWorkflowStore.getState();
        if (current.workflowId !== workflowId) return;
        const snapshot = graphSnapshot(current.nodes, current.edges);
        const text = message.trim();
        const fullRequest = clarificationContext ? `${clarificationContext}\nAnswer: ${text}` : text;
        if (fullRequest.length > 4000) {
            setError("Please restate the full change in under 4,000 characters.");
            setClarificationContext("");
            return;
        }
        setSending(true);
        setError("");
        setNotice("");
        setProposal(null);
        setRequest(text);
        setBase(snapshot);
        try {
            const response = await client.post({
                url: "/api/v1/agent-builder/edit-proposal",
                body: { workflow_id: workflowId, message: fullRequest, graph: { nodes: current.nodes, edges: current.edges } },
            });
            if (token !== generation.current) return;
            if (response.error) {
                setError(detailFromError(response.error, "Could not prepare the edit. Your draft is unchanged."));
                return;
            }
            const data = response.data as Proposal | undefined;
            if (!data || !["proposal", "clarification"].includes(data.status) || typeof data.summary !== "string" || !Array.isArray(data.changes) || (data.status === "proposal" && (!Array.isArray(data.graph?.nodes) || !Array.isArray(data.graph?.edges)))) {
                throw new Error("The edit response was incomplete. Your draft is unchanged.");
            }
            setProposal(data);
            setClarificationContext(data.status === "clarification" ? `${fullRequest}\nDecibyl asked: ${data.summary}` : "");
            if (data.status === "clarification") setMessage("");
        } catch (err) {
            if (token === generation.current) setError(detailFromError(err, "Could not prepare the edit. Your draft is unchanged."));
        } finally {
            if (token === generation.current) setSending(false);
        }
    }

    function apply() {
        if (readOnly || !proposal?.graph) return;
        if (!applyGraphProposal(workflowId, base, proposal.graph.nodes, proposal.graph.edges)) {
            setError("The draft changed. Request a new proposal before applying.");
            return;
        }
        setProposal(null);
        setClarificationContext("");
        setNotice("Applied to your draft. Review in Graph, then Save when ready.");
    }

    return <section aria-label="Edit agent by chat" className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-6 py-8">
        <div><h2 className="text-xl font-semibold">Edit with Decibyl</h2><p className="mt-2 text-sm text-muted-foreground">Describe changes to this agent’s instructions or greeting. Use Graph to add steps, connect tools, and change the flow.</p></div>
        <div className="flex gap-2" aria-label="Draft history">
            <Button variant="outline" size="sm" disabled={readOnly || !ready || !canUndo} onClick={() => { undo(); setNotice("Undid the last draft edit."); }}>Undo</Button>
            <Button variant="outline" size="sm" disabled={readOnly || !ready || !canRedo} onClick={() => { redo(); setNotice("Redid the draft edit."); }}>Redo</Button>
        </div>
        {readOnly && <p role="status">Return to the draft to edit this version.</p>}
        {!ready && <p role="status">Loading this agent’s draft…</p>}
        {request && <div className="rounded-xl border bg-muted/30 p-4"><p className="text-xs font-medium text-muted-foreground">Your request</p><p className="mt-2 whitespace-pre-wrap">{request}</p></div>}
        {sending && <p role="status">Preparing an edit from your current draft…</p>}
        {proposal && <div className="rounded-xl border p-4">
            <p className="whitespace-pre-wrap">{proposal.summary}</p>
            {proposal.changes.length > 0 && <ul className="mt-4 space-y-4">{proposal.changes.map((change, index) => <li key={`${change.node_id}-${change.field}-${index}`} className="rounded-lg bg-muted/30 p-3 text-sm"><p className="font-medium">{nodes.find((node) => node.id === change.node_id)?.data.name || change.node_id} / {change.field === "prompt" ? "Instructions" : change.field === "greeting_type" ? "Greeting format" : "Greeting"}</p><p className="mt-2 whitespace-pre-wrap"><span className="text-muted-foreground">Before: </span>{change.before}</p><p className="mt-2 whitespace-pre-wrap"><span className="text-muted-foreground">After: </span>{change.after}</p></li>)}</ul>}
            {stale && <p role="status" className="mt-3 text-sm">Your draft changed since this proposal. Request a new edit.</p>}
            {proposal.status === "proposal" && <div className="mt-4 flex gap-2"><Button onClick={apply} disabled={readOnly || stale}>Apply to draft</Button><Button variant="outline" onClick={() => { setProposal(null); setNotice("Proposal discarded. Your draft is unchanged."); }}>Discard</Button></div>}
        </div>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {notice && <p role="status" className="text-sm">{notice}</p>}
        <form onSubmit={(event) => { event.preventDefault(); void propose(); }} className="space-y-3">
            <label htmlFor="agent-edit-request" className="text-sm font-medium">What should change?</label>
            <Textarea id="agent-edit-request" value={message} onChange={(event) => setMessage(event.target.value)} disabled={readOnly || !ready || sending} placeholder="Make the greeting warmer and ask for their name first…" maxLength={4000} />
            <div className="flex items-center justify-between gap-4"><p className="text-xs text-muted-foreground">Review before applying. Changes stay in this draft until you save. Uses your builder message allowance.</p><Button type="submit" disabled={loading || !user || readOnly || !ready || sending || !message.trim()}>Propose edit</Button></div>
        </form>
    </section>;
}
