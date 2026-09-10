"use client";

/**
 * "Share": the link a prospect opens to talk to this agent, no account.
 *
 * One press makes (or reactivates) the agent's public token and shows the
 * link with a copy button. Revoking is on the Web widget page, because the
 * link and the widget are the same token — one thing to switch off.
 */

import { Check, Copy, Link2 } from "lucide-react";
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
import { detailFromResult } from "@/lib/apiError";

export function ShareAgentDialog({ workflowId }: { workflowId: number }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const share = async () => {
    setOpen(true);
    if (url) return;
    setBusy(true);
    const result = await client.post({ url: `/api/v1/workflow/${workflowId}/share-link` });
    setBusy(false);
    if (result.error || !result.data) {
      toast.error(detailFromResult(result, "Could not make a share link."));
      setOpen(false);
      return;
    }
    setUrl((result.data as { url: string }).url);
  };

  const copy = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Could not copy. Select the link and copy it.");
    }
  };

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => void share()} data-testid="share-agent">
        <Link2 className="h-4 w-4" />
        Share
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Share this agent</DialogTitle>
            <DialogDescription>
              Anyone with the link can talk to the agent in their browser, no account. Calls are
              recorded and paid from your credits. Switch it off from Web widget.
            </DialogDescription>
          </DialogHeader>
          {busy || !url ? (
            <p className="text-sm text-muted-foreground">Making the link…</p>
          ) : (
            <div className="flex items-center gap-2">
              <Input readOnly value={url} onFocus={(e) => e.currentTarget.select()} data-testid="share-url" />
              <Button size="sm" onClick={() => void copy()} aria-label="Copy link">
                {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

export default ShareAgentDialog;
