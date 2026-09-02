"use client";

/**
 * Rings on callback-mode numbers, and what happened to each.
 *
 * The refusals are the reason this screen exists. A callback that connects
 * becomes an ordinary call and shows up in call history like any other; a
 * callback we declined — cooldown, daily cap, loop guard, closed calling
 * window — produces no call at all. Without this list a quiet dashboard and a
 * dashboard that is silently declining every caller look identical, and the
 * operator has no way to tell whether the number on their hoarding is working.
 */

import { PhoneIncoming, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { listMissedCallsApiV1MissedCallsGet } from "@/client/sdk.gen";
import type { MissedCallOut } from "@/client/types.gen";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { TelephonyTabs } from "@/components/telephony/TelephonyTabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const OUTCOMES: Record<
  string,
  { label: string; variant: "default" | "secondary" | "destructive" | "outline" }
> = {
  called_back: { label: "Called back", variant: "default" },
  refused: { label: "Not called", variant: "secondary" },
  // A row stuck on pending means the worker never picked the job up. That is a
  // real failure and it is worth looking as alarming as one.
  pending: { label: "Queued", variant: "outline" },
  failed: { label: "Failed", variant: "destructive" },
};

function formatCaller(caller: string) {
  // Stored in the normalised form the DND list and the loop guard compare on,
  // which is digits only. Displayed with a + so it reads as a phone number.
  return caller.startsWith("+") ? caller : `+${caller}`;
}

export default function MissedCallsPage() {
  const [rows, setRows] = useState<MissedCallOut[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const res = await listMissedCallsApiV1MissedCallsGet({ query: { limit: 100 } });
      setRows(res.data ?? []);
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <PageHeader
        title="Missed calls"
        description="Someone rang a callback number and hung up. Here is what happened next."
      />
      <TelephonyTabs />
      <PageBody>
        <div className="mb-4 flex justify-end">
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={refreshing}>
            <RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>

        {rows === null ? (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : rows.length === 0 ? (
          <div className="rounded-lg border border-border p-10 text-center">
            <PhoneIncoming className="mx-auto mb-3 h-6 w-6 text-muted-foreground" />
            <p className="font-medium">No missed calls yet</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Put a number in callback mode, print it somewhere, and rings will appear here.
            </p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Caller</TableHead>
                <TableHead>Rang at</TableHead>
                <TableHead>Outcome</TableHead>
                <TableHead>Detail</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => {
                const outcome = OUTCOMES[row.outcome] ?? {
                  label: row.outcome,
                  variant: "outline" as const,
                };
                return (
                  <TableRow key={row.id}>
                    <TableCell className="font-mono">{formatCaller(row.caller)}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {new Date(row.received_at).toLocaleString()}
                    </TableCell>
                    <TableCell>
                      <Badge variant={outcome.variant}>{outcome.label}</Badge>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {row.refusal_reason ??
                        (row.workflow_run_id ? `Call #${row.workflow_run_id}` : "—")}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </PageBody>
    </>
  );
}
