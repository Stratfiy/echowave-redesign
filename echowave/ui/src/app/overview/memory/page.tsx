"use client";

/**
 * What the business has taught its workers, as a picture you can act on.
 *
 * The knowledge graph's people and things, drawn with the facts between
 * them. A node is confirmed when the account's own record holds one of its
 * facts and inferred when only a conversation said so; inferred is drawn
 * faint, because a picture that made a guess look as solid as a fact would
 * be the graph lying with its whole face. Click a node to see what it is
 * connected to and the conversations that said so.
 *
 * Two buttons, because this is the trust screen: take it all away as a
 * folder of linked notes Obsidian opens, or delete it all. The delete asks
 * for a typed phrase rather than a click; it is the one thing here that
 * cannot be undone.
 */

import "@xyflow/react/dist/style.css";

import {
    Background,
    BackgroundVariant,
    type Edge,
    type Node,
    ReactFlow,
} from "@xyflow/react";
import { Download, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
    forgetEverythingApiV1OrganisationMemoryForgetEverythingPost,
    memoryGraphApiV1OrganisationMemoryGraphGet,
    memoryNodeApiV1OrganisationMemoryGraphNodeIdGet,
    requestExportApiV1OrganisationMemoryExportPost,
} from "@/client/sdk.gen";
import type {
    MemoryGraphResponse,
    MemoryNodeDetail,
} from "@/client/types.gen";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
} from "@/components/ui/sheet";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const PHRASE = "delete everything";

const TABS = [
    { href: "/overview", label: "Messages" },
    { href: "/review", label: "History", prefix: true },
    { href: "/overview/memory", label: "Memory" },
    { href: "/overview/about", label: "About" },
];

/**
 * Concentric rings by how connected a node is: the busiest in the middle,
 * one-offs at the edge. Not a force layout, on purpose -- it is
 * deterministic, so the picture is the same the second time somebody
 * opens it, and a person can find the node they saw yesterday.
 */
function layoutRings(
    nodes: MemoryGraphResponse["nodes"],
): Map<string, { x: number; y: number }> {
    const sorted = [...nodes].sort(
        (a, b) =>
            (b.connections ?? 0) - (a.connections ?? 0) || a.label.localeCompare(b.label),
    );
    const positions = new Map<string, { x: number; y: number }>();
    let ring = 0;
    let index = 0;
    while (index < sorted.length) {
        const capacity = ring === 0 ? 1 : ring * 6;
        const radius = ring * 190;
        const slice = sorted.slice(index, index + capacity);
        slice.forEach((node, i) => {
            const angle = (2 * Math.PI * i) / slice.length - Math.PI / 2;
            positions.set(node.id, {
                x: Math.round(radius * Math.cos(angle)),
                y: Math.round(radius * Math.sin(angle)),
            });
        });
        index += capacity;
        ring += 1;
    }
    return positions;
}

function toFlow(graph: MemoryGraphResponse): { nodes: Node[]; edges: Edge[] } {
    const positions = layoutRings(graph.nodes);
    const nodes: Node[] = graph.nodes.map((node) => {
        const faint = node.status !== "confirmed";
        return {
            id: node.id,
            position: positions.get(node.id) ?? { x: 0, y: 0 },
            data: { label: node.label },
            draggable: false,
            connectable: false,
            style: {
                opacity: faint ? 0.45 : 1,
                borderStyle: faint ? "dashed" : "solid",
                borderColor: faint ? "var(--muted-foreground)" : "var(--primary)",
                background: "var(--card)",
                color: "var(--foreground)",
                fontSize: 12,
                padding: "6px 10px",
                borderRadius: 999,
                width: "auto",
            },
        };
    });
    const edges: Edge[] = graph.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: edge.relation.toLowerCase().replace(/_/g, " "),
        style: {
            opacity: edge.status === "confirmed" ? 0.9 : 0.35,
            strokeDasharray: edge.status === "confirmed" ? undefined : "4 3",
        },
        labelStyle: { fontSize: 10, opacity: edge.status === "confirmed" ? 1 : 0.6 },
        animated: false,
    }));
    return { nodes, edges };
}

export default function MemoryPage() {
    const { user, loading: authLoading } = useAuth();
    const [graph, setGraph] = useState<MemoryGraphResponse | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [open, setOpen] = useState<MemoryNodeDetail | null>(null);
    const [openBusy, setOpenBusy] = useState(false);
    const [exportNote, setExportNote] = useState<string | null>(null);
    const [confirm, setConfirm] = useState("");
    const [deleting, setDeleting] = useState(false);
    const [deleteOpen, setDeleteOpen] = useState(false);

    const load = useCallback(async () => {
        const response = await memoryGraphApiV1OrganisationMemoryGraphGet();
        if (response.error || !response.data) {
            setError(detailFromError(response.error) ?? "Could not read the memory.");
            setGraph({ nodes: [], edges: [], graph_available: false, records: 0 });
            return;
        }
        setError(null);
        setGraph(response.data);
    }, []);

    useEffect(() => {
        if (authLoading || !user) return;
        void load();
    }, [authLoading, user, load]);

    const flow = useMemo(() => (graph ? toFlow(graph) : { nodes: [], edges: [] }), [graph]);

    const openNode = async (id: string) => {
        setOpenBusy(true);
        const response = await memoryNodeApiV1OrganisationMemoryGraphNodeIdGet({
            path: { node_id: id },
        });
        setOpenBusy(false);
        if (response.error || !response.data) {
            setError(detailFromError(response.error) ?? "Could not open that.");
            return;
        }
        setOpen(response.data);
    };

    const requestExport = async () => {
        setExportNote(null);
        const response = await requestExportApiV1OrganisationMemoryExportPost();
        if (response.error || !response.data) {
            setExportNote(detailFromError(response.error) ?? "Could not start the export.");
            return;
        }
        setExportNote(`Sent to ${response.data.sent_to}. ${response.data.note}`);
    };

    const deleteAll = async () => {
        setDeleting(true);
        const response = await forgetEverythingApiV1OrganisationMemoryForgetEverythingPost({
            body: { confirm },
        });
        setDeleting(false);
        if (response.error || !response.data) {
            setError(detailFromError(response.error) ?? "Could not delete.");
            return;
        }
        setDeleteOpen(false);
        setConfirm("");
        setOpen(null);
        setExportNote(response.data.note);
        await load();
    };

    const confirmedCount = graph?.nodes.filter((n) => n.status === "confirmed").length ?? 0;

    return (
        <>
            <PageHeader
                title="Decibyl"
                description="Your team's assistant. Ask what happened, or build a new bot."
                tabs={TABS}
                actions={
                    <>
                        <Button variant="outline" size="sm" onClick={requestExport}>
                            <Download className="mr-1.5 h-4 w-4" aria-hidden />
                            Export to Obsidian
                        </Button>
                        <Button
                            variant="outline"
                            size="sm"
                            className="text-destructive"
                            onClick={() => setDeleteOpen(true)}
                        >
                            <Trash2 className="mr-1.5 h-4 w-4" aria-hidden />
                            Delete everything
                        </Button>
                    </>
                }
            />
            <PageBody className="space-y-4">
                <p className="text-sm text-muted-foreground">
                    What this business has taught its workers.{" "}
                    {graph && (
                        <>
                            {graph.nodes.length} people and things, {graph.edges.length} facts
                            between them, {graph.records} remembered on the About page.
                            Solid means a person confirmed it; faint means a conversation
                            suggested it and nobody has yet.
                        </>
                    )}
                </p>
                {exportNote && (
                    <p className="text-sm" role="status">
                        {exportNote}
                    </p>
                )}
                {error && (
                    <p className="text-sm text-destructive" role="alert">
                        {error}
                    </p>
                )}
                {graph && !graph.graph_available && (
                    <p className="text-sm text-muted-foreground">
                        The knowledge graph is not reachable right now; the remembered facts
                        on the About page are still here.
                    </p>
                )}
                {graph && graph.graph_available && graph.nodes.length === 0 && (
                    <p className="text-sm text-muted-foreground">
                        Nothing here yet. It fills in as calls, messages and documents come
                        through.
                    </p>
                )}
                <div
                    className="h-[560px] w-full rounded-lg border bg-card"
                    data-testid="memory-graph"
                    data-confirmed={confirmedCount}
                >
                    <ReactFlow
                        nodes={flow.nodes}
                        edges={flow.edges}
                        fitView
                        nodesDraggable={false}
                        nodesConnectable={false}
                        elementsSelectable
                        onNodeClick={(_, node) => void openNode(node.id)}
                        proOptions={{ hideAttribution: true }}
                    >
                        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
                    </ReactFlow>
                </div>
            </PageBody>

            <Sheet open={open !== null || openBusy} onOpenChange={(o) => !o && setOpen(null)}>
                <SheetContent className="overflow-y-auto sm:max-w-md">
                    {open && (
                        <>
                            <SheetHeader>
                                <SheetTitle>{open.label}</SheetTitle>
                                <SheetDescription>
                                    {open.status === "confirmed"
                                        ? "Confirmed by a person."
                                        : "Inferred from conversations; nobody has confirmed it."}
                                    {open.summary ? ` ${open.summary}` : ""}
                                </SheetDescription>
                            </SheetHeader>
                            <div className="mt-4 space-y-4">
                                <section>
                                    <h3 className="text-sm font-semibold">Connections</h3>
                                    <ul className="mt-1 space-y-1 text-sm">
                                        {open.connections.map((c) => (
                                            <li
                                                key={c.id}
                                                className={c.current ? "" : "line-through opacity-60"}
                                                style={{ opacity: c.status === "confirmed" ? 1 : 0.6 }}
                                            >
                                                <button
                                                    type="button"
                                                    className="underline-offset-2 hover:underline"
                                                    onClick={() => void openNode(c.other_id)}
                                                >
                                                    {c.other}
                                                </button>
                                                {" — "}
                                                {c.fact}
                                                {c.valid_at ? ` (${c.valid_at.slice(0, 10)})` : ""}
                                            </li>
                                        ))}
                                        {open.connections.length === 0 && (
                                            <li className="text-muted-foreground">Nothing yet.</li>
                                        )}
                                    </ul>
                                </section>
                                <section>
                                    <h3 className="text-sm font-semibold">Where it came from</h3>
                                    <ul className="mt-1 space-y-2 text-sm">
                                        {open.sources.map((s) => (
                                            <li key={s.id} className="rounded-md border p-2">
                                                <p className="text-xs text-muted-foreground">
                                                    {s.name}
                                                    {s.when ? ` · ${s.when.slice(0, 10)}` : ""}
                                                    {s.run_id ? (
                                                        <>
                                                            {" · "}
                                                            <a
                                                                className="underline"
                                                                href={`/review?run=${s.run_id}`}
                                                            >
                                                                open
                                                            </a>
                                                        </>
                                                    ) : null}
                                                </p>
                                                <p className="mt-1 whitespace-pre-wrap">{s.excerpt}</p>
                                            </li>
                                        ))}
                                        {open.sources.length === 0 && (
                                            <li className="text-muted-foreground">
                                                No conversation on record for this.
                                            </li>
                                        )}
                                    </ul>
                                </section>
                            </div>
                        </>
                    )}
                </SheetContent>
            </Sheet>

            <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete everything the business has taught its workers?</AlertDialogTitle>
                        <AlertDialogDescription>
                            Every person, thing, fact, gap and conversation in memory goes, and
                            it cannot be undone. Export first if you want a copy. Type{" "}
                            <span className="font-mono">{PHRASE}</span> to confirm.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <Input
                        value={confirm}
                        onChange={(e) => setConfirm(e.target.value)}
                        placeholder={PHRASE}
                        aria-label="Confirmation phrase"
                        autoFocus
                    />
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={deleting}>Keep it</AlertDialogCancel>
                        <AlertDialogAction
                            disabled={deleting || confirm.trim().toLowerCase() !== PHRASE}
                            onClick={(e) => {
                                e.preventDefault();
                                void deleteAll();
                            }}
                        >
                            {deleting ? "Deleting…" : "Delete everything"}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </>
    );
}
