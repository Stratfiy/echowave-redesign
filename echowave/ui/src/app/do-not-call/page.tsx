"use client";

import { AlertTriangle, Plus, Search, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  addNumbersApiV1DoNotCallPost,
  listNumbersApiV1DoNotCallGet,
  removeNumberApiV1DoNotCallPhoneNumberDelete,
  uploadNumbersApiV1DoNotCallUploadPost,
} from "@/client/sdk.gen";
import type { DoNotCallEntry } from "@/client/types.gen";
import { useConfirm } from "@/components/ConfirmDialog";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const PAGE_SIZE = 100;

/**
 * The do-not-disturb list.
 *
 * The backend refuses calls to anything listed here (services/compliance/dnd.py);
 * this screen is how a customer puts numbers on it. Neither half is useful
 * alone — enforcement with no way to add a number protects nobody, and a list
 * nothing reads is a spreadsheet.
 */
export default function DoNotCallPage() {
  const roles = useAccessRoles();
  const { user, loading: authLoading } = useAuth();

  const { confirm, dialog } = useConfirm();
  const [entries, setEntries] = useState<DoNotCallEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const [addOpen, setAddOpen] = useState(false);
  const [addValue, setAddValue] = useState("");
  const [addNote, setAddNote] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(
    async (nextOffset: number) => {
      setIsLoading(true);
      setError(null);
      const response = await listNumbersApiV1DoNotCallGet({
        query: { limit: PAGE_SIZE, offset: nextOffset },
      });
      if (response.error) {
        setError(detailFromResult(response, "Could not load the list."));
        setIsLoading(false);
        return;
      }
      setEntries(response.data?.entries ?? []);
      setTotal(response.data?.total ?? 0);
      setOffset(nextOffset);
      setIsLoading(false);
    },
    []
  );

  useEffect(() => {
    if (authLoading || !user) return;
    void load(0);
  }, [authLoading, user, load]);

  /** Report what was added AND what was not. Telling someone "4,000 added"
   *  when 300 rows were unusable leaves them believing they are covered for
   *  numbers they are not. */
  const describeResult = (result: {
    added: number;
    already_listed: number;
    rejected: string[];
  }) => {
    const parts = [`${result.added} added`];
    if (result.already_listed > 0) parts.push(`${result.already_listed} already listed`);
    if (result.rejected.length > 0) {
      parts.push(`${result.rejected.length} not recognised as phone numbers`);
    }
    return parts.join(" · ");
  };

  const handleAdd = async () => {
    const numbers = addValue
      .split(/[\s,;]+/)
      .map((value) => value.trim())
      .filter(Boolean);
    if (numbers.length === 0) return;

    setIsSubmitting(true);
    const response = await addNumbersApiV1DoNotCallPost({
      body: { phone_numbers: numbers, note: addNote || null },
    });
    setIsSubmitting(false);

    if (response.error) {
      setError(detailFromResult(response, "Could not add those numbers."));
      return;
    }
    setNotice(describeResult(response.data!));
    setAddOpen(false);
    setAddValue("");
    setAddNote("");
    void load(0);
  };

  const handleUpload = async (file: File) => {
    setIsSubmitting(true);
    setError(null);
    const response = await uploadNumbersApiV1DoNotCallUploadPost({
      body: { file },
    });
    setIsSubmitting(false);

    if (response.error) {
      setError(detailFromResult(response, "Could not read that file."));
      return;
    }
    setNotice(describeResult(response.data!));
    void load(0);
  };

  const handleRemove = async (phoneNumber: string) => {
    // The most consequential single click in the product, and until now it
    // took effect immediately with no prompt. Taking a number off this list
    // does not lose data — it makes it legal-facing: the next campaign will
    // dial somebody who asked not to be dialled, and under the TRAI rules
    // that is a regulatory incident rather than a support ticket. The person
    // who opted out is not around to notice the mistake, so the confirmation
    // is the only chance anyone gets to catch it.
    const ok = await confirm({
      title: `Allow calls to ${phoneNumber} again?`,
      description:
        "This number opted out of being contacted. Removing it from the Do Not Call list means campaigns and agents will dial it again. Only do this if you have a record of them asking to be contacted.",
      confirmLabel: "Remove from list",
      destructive: true,
    });
    if (!ok) return;

    const response = await removeNumberApiV1DoNotCallPhoneNumberDelete({
      path: { phone_number: phoneNumber },
    });
    if (response.error) {
      setError(detailFromResult(response, "Could not remove that number."));
      return;
    }
    setNotice(`${phoneNumber} removed — calls to it are allowed again.`);
    void load(offset);
  };

  const visible = filter.trim()
    ? entries.filter((entry) => entry.phone_number.includes(filter.replace(/\D/g, "")))
    : entries;

  return (
    <>
      {dialog}
      <PageHeader
        title="Do not call"
        description="Numbers this account will never dial. Checked immediately before every call, including every row of a campaign."
        actions={
          <>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void handleUpload(file);
                event.target.value = "";
              }}
            />
            <Button
              variant="outline"
              onClick={() => fileInputRef.current?.click()}
              disabled={isSubmitting}
            >
              <Upload className="h-4 w-4" />
              Upload CSV
            </Button>
            <Button onClick={() => setAddOpen(true)}>
              <Plus className="h-4 w-4" />
              Add numbers
            </Button>
          </>
        }
      />

      <PageBody className="space-y-4">
        {notice && (
          <div className="rounded-[var(--radius-control)] border border-border bg-card px-4 py-3 text-sm">
            {notice}
          </div>
        )}
        {error && (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-[var(--radius-control)] border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <Card>
          <CardHeader className="gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle>
                {total} {total === 1 ? "number" : "numbers"}
              </CardTitle>
              <CardDescription>
                Calling hours are enforced separately: outbound runs between 9am
                and 9pm in your organization&apos;s timezone.
              </CardDescription>
            </div>
            <div className="relative w-full sm:w-64">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder="Filter this page"
                aria-label="Filter numbers on this page"
                className="pl-9"
              />
            </div>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 5 }, (_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : visible.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                {total === 0
                  ? "No numbers suppressed yet. Add the ones you have been asked not to call."
                  : "No numbers on this page match that filter."}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Number</TableHead>
                      <TableHead>Added</TableHead>
                      <TableHead>Source</TableHead>
                      <TableHead>Note</TableHead>
                      <TableHead className="w-16" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {visible.map((entry) => (
                      <TableRow key={entry.phone_number}>
                        <TableCell className="font-mono">+{entry.phone_number}</TableCell>
                        <TableCell className="text-muted-foreground">
                          {entry.created_at
                            ? new Date(entry.created_at).toLocaleDateString()
                            : "—"}
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {entry.source}
                        </TableCell>
                        <TableCell className="max-w-xs truncate text-muted-foreground">
                          {entry.note || "—"}
                        </TableCell>
                        <TableCell>
                          {/* Only removal is admin-gated, and deliberately:
                              taking a number off the suppression list is a
                              regulatory act and the one entry nobody notices
                              going missing. Listing and adding stay open to
                              every member, so the screen itself is not
                              gated — just this control. */}
                          {roles.isOrganizationAdmin ? (
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`Remove ${entry.phone_number}`}
                              onClick={() => void handleRemove(entry.phone_number)}
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          ) : (
                            <span
                              className="text-xs text-muted-foreground"
                              title="Only an organization Admin or Owner can remove a number from this list."
                            >
                              Admin only
                            </span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}

            {total > PAGE_SIZE && (
              <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
                <span>
                  {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
                </span>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={offset === 0 || isLoading}
                    onClick={() => void load(Math.max(0, offset - PAGE_SIZE))}
                  >
                    Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={offset + PAGE_SIZE >= total || isLoading}
                    onClick={() => void load(offset + PAGE_SIZE)}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </PageBody>

      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add numbers</DialogTitle>
            <DialogDescription>
              One per line, or separated by commas. Any format works — +91 98765
              43210 and 09876543210 are stored as the same number.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="dnc-numbers">Numbers</Label>
              <Textarea
                id="dnc-numbers"
                value={addValue}
                onChange={(event) => setAddValue(event.target.value)}
                placeholder={"9876543210\n+91 98765 43211"}
                rows={6}
                className="font-mono text-sm"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="dnc-note">Note (optional)</Label>
              <Input
                id="dnc-note"
                value={addNote}
                onChange={(event) => setAddNote(event.target.value)}
                placeholder="Asked to be removed on the call"
                maxLength={255}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => void handleAdd()} disabled={isSubmitting || !addValue.trim()}>
              Add
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
