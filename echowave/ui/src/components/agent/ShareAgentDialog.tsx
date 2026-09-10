"use client";

/**
 * "Share": the link a prospect opens to talk to this agent, no account.
 *
 * The link goes to strangers and every call on it is paid from the owner's
 * credits, so the dialog shows what it may spend — minutes a day, an expiry
 * — lets the owner change both, and has the switch. Making the link again
 * after switching it off turns it back on with a fresh expiry.
 */

import { Check, Copy, Link2, Power } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromResult } from "@/lib/apiError";

export type ShareLink = {
  url: string;
  token: string;
  is_active: boolean;
  expires_at: string | null;
  daily_minutes_cap: number | null;
  minutes_used_today: number;
};

const CAP_CHOICES: Array<{ value: string; label: string }> = [
  { value: "15", label: "15 minutes a day" },
  { value: "30", label: "30 minutes a day" },
  { value: "60", label: "1 hour a day" },
  { value: "120", label: "2 hours a day" },
  { value: "none", label: "No daily limit" },
];

const EXPIRY_CHOICES: Array<{ value: string; label: string }> = [
  { value: "7", label: "In 7 days" },
  { value: "30", label: "In 30 days" },
  { value: "90", label: "In 90 days" },
  { value: "never", label: "Never" },
];

/** The expiry choice a stored date is closest to, for the select's value. */
export function expiryChoiceFor(expiresAt: string | null, now = new Date()): string {
  if (!expiresAt) return "never";
  const days = Math.round((new Date(expiresAt).getTime() - now.getTime()) / 86_400_000);
  const choices = [7, 30, 90];
  const nearest = choices.reduce((best, c) => (Math.abs(c - days) < Math.abs(best - days) ? c : best));
  return String(nearest);
}

export function describeUsage(link: ShareLink): string {
  if (link.daily_minutes_cap === null) return `${link.minutes_used_today} min used today, no daily limit`;
  return `${link.minutes_used_today} of ${link.daily_minutes_cap} min used today`;
}

export function ShareAgentDialog({ workflowId }: { workflowId: number }) {
  const [open, setOpen] = useState(false);
  const [link, setLink] = useState<ShareLink | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const base = `/api/v1/workflow/${workflowId}/share-link`;

  const load = async () => {
    setOpen(true);
    if (link) return;
    setBusy(true);
    const existing = await client.get({ url: base });
    if (!existing.error && existing.data) {
      setLink(existing.data as ShareLink);
      setBusy(false);
      return;
    }
    await make();
  };

  const make = async () => {
    setBusy(true);
    const result = await client.post({ url: base });
    setBusy(false);
    if (result.error || !result.data) {
      toast.error(detailFromResult(result, "Could not make a share link."));
      return;
    }
    setLink(result.data as ShareLink);
  };

  const update = async (body: Record<string, unknown>) => {
    const result = await client.put({ url: base, body });
    if (result.error || !result.data) {
      toast.error(detailFromResult(result, "Could not change the link."));
      return;
    }
    setLink(result.data as ShareLink);
  };

  const switchOff = async () => {
    const result = await client.delete({ url: base });
    if (result.error || !result.data) {
      toast.error(detailFromResult(result, "Could not switch the link off."));
      return;
    }
    setLink(result.data as ShareLink);
    toast.success("Link switched off. Nobody can start a call on it.");
  };

  const copy = async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Could not copy. Select the link and copy it.");
    }
  };

  const capValue = link?.daily_minutes_cap === null ? "none" : String(link?.daily_minutes_cap ?? 30);
  const selectClass =
    "h-9 w-full rounded-md border border-input bg-background px-3 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring";

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => void load()} data-testid="share-agent">
        <Link2 className="h-4 w-4" />
        Share
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Share this agent</DialogTitle>
            <DialogDescription>
              Anyone with the link can talk to the agent in their browser, no account. Calls are
              recorded and paid from your credits, so the link has a daily limit and an expiry.
            </DialogDescription>
          </DialogHeader>
          {busy || !link ? (
            <p className="text-sm text-muted-foreground">Making the link…</p>
          ) : !link.is_active ? (
            <div className="space-y-3">
              <p className="text-sm" data-testid="share-off">
                This link is switched off. Nobody can start a call on it.
              </p>
              <Button size="sm" onClick={() => void make()} data-testid="share-on">
                <Power className="h-4 w-4" />
                Switch it back on
              </Button>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <Input readOnly value={link.url} onFocus={(e) => e.currentTarget.select()} data-testid="share-url" />
                <Button size="sm" onClick={() => void copy()} aria-label="Copy link">
                  {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground" data-testid="share-usage">
                {describeUsage(link)}
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1">
                  <Label htmlFor="share-cap" className="text-xs">Daily limit</Label>
                  <select
                    id="share-cap"
                    className={selectClass}
                    value={capValue}
                    data-testid="share-cap"
                    onChange={(e) =>
                      void update(
                        e.target.value === "none"
                          ? { lift_cap: true }
                          : { daily_minutes_cap: Number(e.target.value) },
                      )
                    }
                  >
                    {CAP_CHOICES.map((c) => (
                      <option key={c.value} value={c.value}>{c.label}</option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="share-expiry" className="text-xs">Link expires</Label>
                  <select
                    id="share-expiry"
                    className={selectClass}
                    value={expiryChoiceFor(link.expires_at)}
                    data-testid="share-expiry"
                    onChange={(e) =>
                      void update(
                        e.target.value === "never"
                          ? { never_expires: true }
                          : { expires_in_days: Number(e.target.value) },
                      )
                    }
                  >
                    {EXPIRY_CHOICES.map((c) => (
                      <option key={c.value} value={c.value}>{c.label}</option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="flex items-center justify-between border-t pt-3">
                <p className="text-xs text-muted-foreground">
                  {link.expires_at
                    ? `Stops on ${new Date(link.expires_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}.`
                    : "Does not expire."}
                </p>
                <Button variant="ghost" size="sm" onClick={() => void switchOff()} data-testid="share-off-button">
                  <Power className="h-4 w-4" />
                  Switch off
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

export default ShareAgentDialog;
