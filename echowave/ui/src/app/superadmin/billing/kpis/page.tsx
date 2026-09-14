"use client";

/**
 * The KPI board — pricing spec §9, every row, three windows.
 *
 * One table per section. Each KPI is a row; each window (today, 7 days,
 * 30 days) is a column showing the current figure and, beneath it, the
 * previous period's, with the change coloured by whichever direction the
 * KPI declares to be good. A row we cannot compute yet is still on the
 * board, greyed, with the reason in place of a number — the reader should
 * never mistake "not measured" for "zero".
 *
 * Superadmin only, and checked here as well as in the layout: the layout
 * gates on staff, which includes support, and this is the founders' screen.
 */

import { AlertTriangle, ChevronDown, ChevronRight, Info } from "lucide-react";
import { useEffect, useState } from "react";

import { getKpiBoardApiV1AdminKpisGet } from "@/client/sdk.gen";
import { LoadingBlock, PanelMessage, useAuthReady } from "@/components/charts/primitives";
import { Badge } from "@/components/ui/badge";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { detailFromResult } from "@/lib/apiError";
import { formatNumber, formatPaise, formatPercent } from "@/lib/billing/format";
import { cn } from "@/lib/utils";

type Cell = { value: number | null; breakdown?: Record<string, unknown> } | null;

type Kpi = {
    key: string;
    label: string;
    definition: string;
    unit: string;
    shape: "flow" | "point";
    available: boolean;
    unavailable_reason: string | null;
    good: "up" | "down" | "none";
    values: Record<string, { current: Cell; previous: Cell }>;
};

type Board = {
    as_of: string;
    windows: { key: string; start: string; end: string; previous_start: string; previous_end: string }[];
    sections: { key: string; title: string; kpis: Kpi[] }[];
    unavailable: { key: string; label: string; reason: string }[];
};

const WINDOW_LABELS: Record<string, string> = {
    day: "Today",
    week: "7 days",
    month: "30 days",
};

function formatValue(unit: string, value: number | null | undefined): string {
    if (value === null || value === undefined) return "—";
    switch (unit) {
        case "paise":
            return formatPaise(value);
        case "ratio":
            return formatPercent(value);
        case "ms":
            return `${formatNumber(Math.round(value))} ms`;
        case "days":
            return `${value.toFixed(1)} d`;
        case "hours":
            return `${value.toFixed(1)} h`;
        case "bps":
            return `${(value / 10_000).toFixed(2)}×`;
        case "minutes":
            return `${formatNumber(value)} min`;
        case "credits":
            return `${formatNumber(value)} cr`;
        default:
            return formatNumber(value);
    }
}

function changeTone(kpi: Kpi, current: number | null, previous: number | null): string {
    if (kpi.good === "none" || current === null || previous === null || current === previous) {
        return "text-muted-foreground";
    }
    const up = current > previous;
    const good = kpi.good === "up" ? up : !up;
    return good ? "text-emerald-600" : "text-red-600";
}

function BreakdownRows({ breakdown }: { breakdown: Record<string, unknown> }) {
    return (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
            {Object.entries(breakdown).map(([key, value]) => (
                <div key={key} className="contents">
                    <dt className="font-medium">{key.replace(/_/g, " ")}</dt>
                    <dd className="truncate font-mono">
                        {typeof value === "object" && value !== null
                            ? JSON.stringify(value)
                            : String(value ?? "—")}
                    </dd>
                </div>
            ))}
        </dl>
    );
}

function KpiRow({ kpi, windows }: { kpi: Kpi; windows: Board["windows"] }) {
    const [open, setOpen] = useState(false);
    const hasBreakdown = kpi.available && Object.values(kpi.values).some((v) => v.current?.breakdown);

    return (
        <>
            <TableRow className={cn(!kpi.available && "opacity-60")}>
                <TableCell className="align-top">
                    <div className="flex items-start gap-1.5">
                        {hasBreakdown ? (
                            <button
                                type="button"
                                onClick={() => setOpen((o) => !o)}
                                className="mt-0.5 text-muted-foreground hover:text-foreground"
                                aria-label={open ? "Hide breakdown" : "Show breakdown"}
                            >
                                {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                            </button>
                        ) : (
                            <span className="w-3.5" />
                        )}
                        <div>
                            <div className="flex items-center gap-1.5 text-sm font-medium">
                                {kpi.label}
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <Info className="h-3.5 w-3.5 text-muted-foreground" />
                                    </TooltipTrigger>
                                    <TooltipContent className="max-w-xs">{kpi.definition}</TooltipContent>
                                </Tooltip>
                                {kpi.shape === "point" ? (
                                    <Badge variant="outline" className="text-[10px]">at window end</Badge>
                                ) : null}
                            </div>
                            {!kpi.available ? (
                                <p className="mt-0.5 flex items-start gap-1 text-xs text-muted-foreground">
                                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-amber-600" />
                                    {kpi.unavailable_reason}
                                </p>
                            ) : null}
                        </div>
                    </div>
                </TableCell>
                {windows.map((w) => {
                    const cell = kpi.values[w.key];
                    const current = cell?.current?.value ?? null;
                    const previous = cell?.previous?.value ?? null;
                    return (
                        <TableCell key={w.key} className="text-right align-top tabular-nums">
                            {kpi.available ? (
                                <>
                                    <div className="text-sm font-medium">{formatValue(kpi.unit, current)}</div>
                                    <div className={cn("text-xs", changeTone(kpi, current, previous))}>
                                        {formatValue(kpi.unit, previous)}
                                    </div>
                                </>
                            ) : (
                                <span className="text-xs text-muted-foreground">not measured</span>
                            )}
                        </TableCell>
                    );
                })}
            </TableRow>
            {open ? (
                <TableRow className="bg-muted/30">
                    <TableCell />
                    {windows.map((w) => (
                        <TableCell key={w.key} className="align-top">
                            {kpi.values[w.key]?.current?.breakdown ? (
                                <BreakdownRows breakdown={kpi.values[w.key].current!.breakdown!} />
                            ) : null}
                        </TableCell>
                    ))}
                </TableRow>
            ) : null}
        </>
    );
}

export default function KpiBoardPage() {
    const ready = useAuthReady();
    const roles = useAccessRoles();
    const [board, setBoard] = useState<Board | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!ready) return;
        let cancelled = false;
        (async () => {
            const result = await getKpiBoardApiV1AdminKpisGet({});
            if (cancelled) return;
            if (result.error || !result.data) {
                setError(detailFromResult(result, "Could not load the KPI board"));
                return;
            }
            setBoard(result.data as unknown as Board);
        })();
        return () => {
            cancelled = true;
        };
    }, [ready]);

    if (!roles.loaded) return null;
    if (roles.staffRole !== "superadmin") {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />}>
                This board is for superadmins.
            </PanelMessage>
        );
    }

    if (error) {
        return <PanelMessage icon={<AlertTriangle className="h-5 w-5" />}>{error}</PanelMessage>;
    }
    if (!board) return <LoadingBlock label="Loading the board" />;

    return (
        <div className="space-y-8">
            <div className="flex flex-wrap items-end justify-between gap-2">
                <div>
                    <h2 className="text-lg font-semibold">KPI board</h2>
                    <p className="text-sm text-muted-foreground">
                        Windows end on {board.as_of} (IST). Each cell shows the window&apos;s figure
                        with the previous period beneath it. Internal accounts are excluded throughout.
                    </p>
                </div>
                {board.unavailable.length > 0 ? (
                    <Badge variant="outline" className="gap-1">
                        <AlertTriangle className="h-3 w-3 text-amber-600" />
                        {board.unavailable.length} not measured yet
                    </Badge>
                ) : null}
            </div>

            {board.sections.map((section) => (
                <section key={section.key} className="space-y-2">
                    <h3 className="text-base font-semibold">{section.title}</h3>
                    <div className="overflow-x-auto rounded-md border">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead className="min-w-[280px]">KPI</TableHead>
                                    {board.windows.map((w) => (
                                        <TableHead key={w.key} className="text-right">
                                            <div>{WINDOW_LABELS[w.key] ?? w.key}</div>
                                            <div className="text-[10px] font-normal text-muted-foreground">
                                                {w.start === w.end ? w.end : `${w.start} → ${w.end}`}
                                            </div>
                                        </TableHead>
                                    ))}
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {section.kpis.map((kpi) => (
                                    <KpiRow key={kpi.key} kpi={kpi} windows={board.windows} />
                                ))}
                            </TableBody>
                        </Table>
                    </div>
                </section>
            ))}
        </div>
    );
}
