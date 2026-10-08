"use client";

import { type ReactNode, useCallback, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export type AssistedAccessChoice = { reason: string; mode: "read_only" | "full" };

/**
 * The one question before staff borrow a customer's session (phase 3): why,
 * and how much. "View as" is read-only and the default -- the API refuses
 * every change made from it; "Impersonate" acts as the person. The reason is
 * kept on the audit row, so it is required.
 */
export function useAssistedAccessDialog(): {
    ask: (who: string) => Promise<AssistedAccessChoice | null>;
    dialog: ReactNode;
} {
    const [who, setWho] = useState<string | null>(null);
    const [reason, setReason] = useState("");
    const [mode, setMode] = useState<"read_only" | "full">("read_only");
    const resolver = useRef<((value: AssistedAccessChoice | null) => void) | null>(null);

    const ask = useCallback((target: string) => {
        setWho(target);
        setReason("");
        setMode("read_only");
        return new Promise<AssistedAccessChoice | null>((resolve) => {
            resolver.current = resolve;
        });
    }, []);

    const close = (value: AssistedAccessChoice | null) => {
        resolver.current?.(value);
        resolver.current = null;
        setWho(null);
    };

    const valid = reason.trim().length >= 3;
    const dialog = (
        <Dialog open={who !== null} onOpenChange={(open) => !open && close(null)}>
            <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg" data-testid="assisted-access-dialog">
                <DialogHeader>
                    <DialogTitle>Open {who ?? "this account"}</DialogTitle>
                    <DialogDescription>
                        A new tab signs in as this person for one hour. It is logged with your reason, shows a banner the whole time, and can be ended from
                        their page in the console.
                    </DialogDescription>
                </DialogHeader>
                <fieldset className="space-y-2">
                    <legend className="text-sm font-medium">How</legend>
                    {(
                        [
                            ["read_only", "View as (read-only)", "See what they see. Every change is refused by the server."],
                            ["full", "Impersonate", "Act as them. Anything you do is theirs."],
                        ] as const
                    ).map(([value, label, hint]) => (
                        <label key={value} className="flex min-h-11 cursor-pointer items-start gap-3 rounded-md border border-border p-3 text-sm">
                            <input
                                type="radio"
                                name="assisted-mode"
                                value={value}
                                checked={mode === value}
                                onChange={() => setMode(value)}
                                className="mt-1"
                            />
                            <span>
                                <span className="font-medium">{label}</span>
                                <span className="block text-muted-foreground">{hint}</span>
                            </span>
                        </label>
                    ))}
                </fieldset>
                <div className="space-y-1">
                    <Label htmlFor="assisted-reason">Reason</Label>
                    <Textarea
                        id="assisted-reason"
                        value={reason}
                        maxLength={300}
                        onChange={(event) => setReason(event.target.value)}
                        placeholder="e.g. ticket 42: checking why the reminder did not send"
                        className="text-base md:text-sm"
                    />
                </div>
                <DialogFooter className="gap-2">
                    <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => close(null)}>
                        Cancel
                    </Button>
                    <Button
                        className="min-h-11 md:min-h-9"
                        variant={mode === "full" ? "destructive" : "default"}
                        disabled={!valid}
                        onClick={() => close({ reason: reason.trim(), mode })}
                    >
                        {mode === "full" ? "Impersonate" : "View as"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
    return { ask, dialog };
}
