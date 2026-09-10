"use client";

/**
 * The tick before a campaign dials.
 *
 * Whether the people on the list agreed to be called is the customer's
 * fact — it lives in their enquiry forms and their DLT registration — and
 * the platform's part is to ask once per campaign and keep the answer with
 * it. The do-not-call list is checked by us regardless.
 *
 * Its own dialog rather than a checkbox under the Start button, and its own
 * sentence rather than "I agree": a complaint about a call arrives months
 * later, and the record has to say what was confirmed.
 */

import { ShieldCheck } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export const CONSENT_STATEMENT =
  "I confirm that everyone on this list has agreed to be called by us for this purpose, " +
  "that the list excludes anyone who asked not to be called, and that any registration the " +
  "telecom rules require for this calling (such as DLT in India) is in place.";

export function ConsentAttestationDialog({
  open,
  onOpenChange,
  onConfirm,
  busy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void | Promise<void>;
  busy?: boolean;
}) {
  const [ticked, setTicked] = useState(false);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setTicked(false);
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5" />
            Before this campaign dials
          </DialogTitle>
          <DialogDescription>
            Outbound calling is the platform calling people who did not call us. We check the
            do-not-call list on every number; the rest is yours to confirm, once per campaign.
          </DialogDescription>
        </DialogHeader>
        <label className="flex items-start gap-3 rounded-lg border border-border bg-muted/30 p-3 text-sm" htmlFor="consent-attest">
          <Checkbox
            id="consent-attest"
            checked={ticked}
            onCheckedChange={(value) => setTicked(value === true)}
            className="mt-0.5"
            data-testid="consent-attest-checkbox"
          />
          <span>{CONSENT_STATEMENT}</span>
        </label>
        <p className="text-xs text-muted-foreground">
          Recorded against your account with the date, and kept with the campaign.
        </p>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            Not yet
          </Button>
          <Button onClick={() => void onConfirm()} disabled={!ticked || busy} data-testid="consent-attest-confirm">
            {busy ? "Starting..." : "Confirm and start"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default ConsentAttestationDialog;
