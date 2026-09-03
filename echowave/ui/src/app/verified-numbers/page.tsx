"use client";

import { AlertTriangle, CheckCircle2, Clock, Phone, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  confirmApiV1VerifiedNumbersConfirmPost,
  listNumbersApiV1VerifiedNumbersGet,
  optionsApiV1VerifiedNumbersOptionsGet,
  removeApiV1VerifiedNumbersPhoneNumberDelete,
  startApiV1VerifiedNumbersStartPost,
} from "@/client/sdk.gen";
import type { VerificationOptions, VerifiedNumber } from "@/client/types.gen";
import { useConfirm } from "@/components/ConfirmDialog";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { TelephonyTabs } from "@/components/telephony/TelephonyTabs";
import { Badge } from "@/components/ui/badge";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

/**
 * Verified numbers.
 *
 * Two jobs on one screen. The obvious one is trial calling: an account with no
 * rented number verifies their own mobile and can hear an agent. The less
 * obvious one is that this is a permission list — a test call now only goes to
 * a number somebody answered, which is why the page explains what verifying
 * unlocks rather than presenting itself as a settings chore.
 */
export default function VerifiedNumbersPage() {
  const { confirm, dialog } = useConfirm();
  const { user, loading: authLoading } = useAuth();

  const [numbers, setNumbers] = useState<VerifiedNumber[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // "enter" is a separate step rather than a second field on the same form:
  // the code does not exist until the first request succeeds, and a form
  // showing both at once invites people to type a code they have not received.
  const [step, setStep] = useState<"closed" | "enter-number" | "enter-code">("closed");
  const [phoneNumber, setPhoneNumber] = useState("");
  const [label, setLabel] = useState("");
  const [code, setCode] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [expiresIn, setExpiresIn] = useState<number | null>(null);
  // What the server normalised the typed number to, and therefore what it is
  // actually calling. The form accepts any format, so echoing the raw input
  // showed a ten-digit Indian mobile back as "+7075701878" — a Russian number,
  // and reason enough to think the wrong phone was being dialled.
  const [dialing, setDialing] = useState("");

  // How this deployment actually delivers a code. Asked rather than assumed:
  // the language picker only means something when we are going to speak, and a
  // control with no effect is worse than no control.
  const [options, setOptions] = useState<VerificationOptions | null>(null);
  const [language, setLanguage] = useState<string>("en-IN");
  const byVoice = options?.channel === "voice";

  const load = useCallback(async () => {
    setIsLoading(true);
    const response = await listNumbersApiV1VerifiedNumbersGet();
    if (response.error) {
      setError(detailFromResult(response, "Could not load your numbers."));
      setIsLoading(false);
      return;
    }
    setNumbers(response.data ?? []);
    setIsLoading(false);
  }, []);

  useEffect(() => {
    if (authLoading || !user) return;
    void load();
  }, [authLoading, user, load]);

  useEffect(() => {
    if (authLoading || !user) return;
    void (async () => {
      const response = await optionsApiV1VerifiedNumbersOptionsGet();
      // A failure here is not worth an error banner. The flow still works; the
      // screen just describes it in general terms instead of naming the channel.
      if (response.data) {
        setOptions(response.data);
        if (response.data.languages.length > 0) {
          setLanguage(response.data.languages[0].code);
        }
      }
    })();
  }, [authLoading, user]);

  // Counts the code's life down. Its only job is to stop someone staring at a
  // code that expired four minutes ago wondering why it will not work.
  useEffect(() => {
    if (step !== "enter-code" || expiresIn === null) return;
    if (expiresIn <= 0) return;
    const timer = setTimeout(() => setExpiresIn((value) => (value ?? 1) - 1), 1000);
    return () => clearTimeout(timer);
  }, [step, expiresIn]);

  const handleStart = async () => {
    setIsSubmitting(true);
    setError(null);
    const response = await startApiV1VerifiedNumbersStartPost({
      body: {
        phone_number: phoneNumber,
        label: label || null,
        language: byVoice ? language : null,
      },
    });
    setIsSubmitting(false);

    if (response.error) {
      setError(detailFromResult(response, "Could not send a code."));
      return;
    }
    setExpiresIn(response.data?.expires_in_seconds ?? null);
    setDialing(response.data?.phone_number ?? phoneNumber.replace(/\D/g, ""));
    setStep("enter-code");
  };

  const handleConfirm = async () => {
    setIsSubmitting(true);
    setError(null);
    const response = await confirmApiV1VerifiedNumbersConfirmPost({
      body: { phone_number: phoneNumber, code },
    });
    setIsSubmitting(false);

    if (response.error) {
      setError(detailFromResult(response, "That code was not accepted."));
      return;
    }
    setNotice(`+${response.data?.phone_number} is verified — you can call it now.`);
    setStep("closed");
    setPhoneNumber("");
    setLabel("");
    setCode("");
    void load();
  };

  const handleRemove = async (number: string) => {
    // Removing a verified number is cheap to undo but slow: the number has to
    // go through the OTP verification round trip again before it can be
    // called. Worth asking, not worth painting red.
    const ok = await confirm({
      title: `Remove +${number}?`,
      description:
        "Test calls to this number will be refused until it is verified again, which means another OTP round trip.",
      confirmLabel: "Remove number",
    });
    if (!ok) return;

    const response = await removeApiV1VerifiedNumbersPhoneNumberDelete({
      path: { phone_number: number },
    });
    if (response.error) {
      setError(detailFromResult(response, "Could not remove that number."));
      return;
    }
    setNotice(`+${number} removed — test calls to it will be refused.`);
    void load();
  };

  const close = () => {
    setStep("closed");
    setError(null);
    setCode("");
  };

  return (
    <>
      {dialog}
      <TelephonyTabs />
      <PageHeader
        title="Verified numbers"
        description="Numbers you have proved you can answer. A test call only goes to a number on this list."
        actions={
          <Button onClick={() => setStep("enter-number")}>
            <Plus className="h-4 w-4" />
            Verify a number
          </Button>
        }
      />

      <PageBody className="space-y-4">
        {notice && (
          <div className="rounded-[var(--radius-control)] border border-border bg-card px-4 py-3 text-sm">
            {notice}
          </div>
        )}
        {error && step === "closed" && (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-[var(--radius-control)] border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Your numbers</CardTitle>
            <CardDescription>
              Verifying proves you can answer the number, so we will dial it for
              a test call. It does not affect campaigns, which dial the numbers
              in your lists.
            </CardDescription>
            {byVoice && (
              <p className="flex items-center gap-2 pt-1 text-sm text-muted-foreground">
                <Phone className="h-4 w-4 shrink-0" />
                We verify by calling you and reading the code out — no SMS.
              </p>
            )}
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 3 }, (_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : numbers.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                No verified numbers yet. Verify your own mobile to hear an agent
                without renting a number first.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Number</TableHead>
                      <TableHead>Label</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="w-16" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {numbers.map((entry) => (
                      <TableRow key={entry.phone_number}>
                        <TableCell className="font-mono">
                          +{entry.phone_number}
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {entry.label || "—"}
                        </TableCell>
                        <TableCell>
                          {entry.status === "verified" ? (
                            <Badge className="gap-1 border-transparent bg-primary/10 text-foreground">
                              <CheckCircle2 className="h-3 w-3 text-primary" />
                              Verified
                            </Badge>
                          ) : (
                            <Badge
                              variant="outline"
                              className="gap-1 text-muted-foreground"
                            >
                              <Clock className="h-3 w-3" />
                              Awaiting code
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Remove ${entry.phone_number}`}
                            onClick={() => void handleRemove(entry.phone_number)}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </PageBody>

      <Dialog open={step !== "closed"} onOpenChange={(open) => !open && close()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {step === "enter-number" ? "Verify a number" : "Enter the code"}
            </DialogTitle>
            <DialogDescription>
              {step === "enter-number"
                ? byVoice
                  ? "We will call this number and read out a six-digit code, twice. Any format works."
                  : "We will send a six-digit code to this number. Any format works."
                : byVoice
                  ? `We are calling +${dialing}. Answer it and write the code down.`
                  : `We sent a code to +${dialing}.`}
            </DialogDescription>
          </DialogHeader>

          {error && (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-[var(--radius-control)] border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {step === "enter-number" ? (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="vn-number">Phone number</Label>
                <Input
                  id="vn-number"
                  value={phoneNumber}
                  onChange={(event) => setPhoneNumber(event.target.value)}
                  placeholder="+91 98765 43210"
                  className="font-mono"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="vn-label">Label (optional)</Label>
                <Input
                  id="vn-label"
                  value={label}
                  onChange={(event) => setLabel(event.target.value)}
                  placeholder="My mobile"
                  maxLength={64}
                />
              </div>
              {byVoice && options && options.languages.length > 0 && (
                <div className="space-y-2">
                  <Label htmlFor="vn-language">Read the code out in</Label>
                  <Select value={language} onValueChange={setLanguage}>
                    <SelectTrigger id="vn-language">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {options.languages.map((entry) => (
                        <SelectItem key={entry.code} value={entry.code}>
                          {entry.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    The digits are read one at a time and repeated once.
                  </p>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              <Label htmlFor="vn-code">Six-digit code</Label>
              <Input
                id="vn-code"
                value={code}
                onChange={(event) =>
                  setCode(event.target.value.replace(/\D/g, "").slice(0, 6))
                }
                placeholder="000000"
                inputMode="numeric"
                autoComplete="one-time-code"
                className="font-mono text-lg tracking-[0.4em]"
              />
              {expiresIn !== null && (
                <p className="text-xs text-muted-foreground">
                  {expiresIn > 0
                    ? `Expires in ${Math.floor(expiresIn / 60)}m ${expiresIn % 60}s.`
                    : "This code has expired. Close this and start again."}
                </p>
              )}
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={close}>
              Cancel
            </Button>
            {step === "enter-number" ? (
              <Button
                onClick={() => void handleStart()}
                disabled={isSubmitting || !phoneNumber.trim()}
              >
                Send code
              </Button>
            ) : (
              <Button
                onClick={() => void handleConfirm()}
                disabled={isSubmitting || code.length < 6}
              >
                Verify
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
