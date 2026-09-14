"use client";

/**
 * Promo codes (KAN-134): make a discount or a bonus-credit code, see what
 * each has cost, and revoke one.
 *
 * Revoking stops new redemptions and never claws back: a customer who typed
 * a valid code was told a price. Every create, change and revoke is a
 * billing audit row on the server.
 */

import { Loader2, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  createPromoCodeApiV1AdminBillingPromoCodesPost,
  listPromoCodesApiV1AdminBillingPromoCodesGet,
  revokePromoCodeApiV1AdminBillingPromoCodesPromoIdRevokePost,
} from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatMinor } from "@/lib/billing/format";

type Promo = {
  id: number;
  code: string;
  kind: "percent" | "amount" | "bonus_credits";
  value: number;
  currency: string | null;
  applies_to: string;
  valid_from: string | null;
  valid_until: string | null;
  max_redemptions: number | null;
  max_per_account: number;
  first_payment_only: boolean;
  active: boolean;
  note: string | null;
  created_at: string | null;
  redemptions: number;
  discount_minor_total: number;
  bonus_credits_total: number;
};

type Draft = {
  code: string;
  kind: Promo["kind"];
  value: string;
  currency: string;
  applies_to: string;
  valid_until: string;
  max_redemptions: string;
  max_per_account: string;
  first_payment_only: boolean;
  note: string;
};

const BLANK: Draft = {
  code: "",
  kind: "percent",
  value: "",
  currency: "INR",
  applies_to: "any",
  valid_until: "",
  max_redemptions: "",
  max_per_account: "1",
  first_payment_only: false,
  note: "",
};

function worth(promo: Promo): string {
  if (promo.kind === "percent") return `${promo.value}% off`;
  if (promo.kind === "amount")
    return `${formatMinor(promo.value, promo.currency ?? "INR")} off`;
  return `+${promo.value.toLocaleString("en-IN")} credits`;
}

function target(applies: string): string {
  if (applies === "any") return "Any purchase";
  if (applies === "any_plan") return "Any plan";
  if (applies === "any_pack") return "Any top-up";
  return applies.replace("plan:", "Plan ").replace("pack:", "Pack ");
}

export default function PromoCodesPage() {
  const { user, loading: authLoading } = useAuth();
  const roles = useAccessRoles();
  const [rows, setRows] = useState<Promo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [saving, setSaving] = useState(false);
  const [revoking, setRevoking] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    const response = await listPromoCodesApiV1AdminBillingPromoCodesGet({});
    if (response.error || !response.data) {
      setError(detailFromResult(response, "Could not load promo codes"));
      return;
    }
    setRows((response.data as unknown as { promo_codes: Promo[] }).promo_codes);
  }, []);

  useEffect(() => {
    if (authLoading || !user) return;
    void refresh();
  }, [authLoading, user, refresh]);

  const create = useCallback(async () => {
    setSaving(true);
    setError(null);
    const value = Number(draft.value);
    const response = await createPromoCodeApiV1AdminBillingPromoCodesPost({
      body: {
        code: draft.code.trim(),
        kind: draft.kind,
        // Amounts are typed in rupees or dollars; the API takes minor units.
        value:
          draft.kind === "amount" ? Math.round(value * 100) : Math.round(value),
        currency: draft.kind === "amount" ? draft.currency : null,
        applies_to: draft.applies_to.trim() || "any",
        valid_until: draft.valid_until
          ? new Date(`${draft.valid_until}T23:59:59+05:30`).toISOString()
          : null,
        max_redemptions: draft.max_redemptions
          ? Number(draft.max_redemptions)
          : null,
        max_per_account: Number(draft.max_per_account) || 1,
        first_payment_only: draft.first_payment_only,
        note: draft.note.trim() || null,
      },
    });
    setSaving(false);
    if (response.error) {
      setError(detailFromResult(response, "Could not create the code"));
      return;
    }
    setDraft(BLANK);
    await refresh();
  }, [draft, refresh]);

  const revoke = useCallback(
    async (promo: Promo) => {
      if (
        !window.confirm(
          `Revoke ${promo.code}? Nothing already granted is taken back.`,
        )
      )
        return;
      setRevoking(promo.id);
      const response =
        await revokePromoCodeApiV1AdminBillingPromoCodesPromoIdRevokePost({
          path: { promo_id: promo.id },
        });
      setRevoking(null);
      if (response.error) {
        setError(detailFromResult(response, "Could not revoke the code"));
        return;
      }
      await refresh();
    },
    [refresh],
  );

  if (roles.staffRole !== "superadmin") {
    return (
      <p className="p-6 text-sm text-muted-foreground">Superadmin only.</p>
    );
  }

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-xl font-semibold">Promo codes</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          A percent or amount off a top-up, or bonus credits on a top-up or a
          plan. GST is charged on the discounted amount and the receipt shows
          the discount line. Revoking stops new uses and never claws back.
        </p>
      </div>

      {error ? (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}

      <section className="rounded-xl border bg-card p-5">
        <h2 className="text-base font-medium">New code</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <Label htmlFor="promo-code">Code</Label>
            <Input
              id="promo-code"
              value={draft.code}
              placeholder="LAUNCH20"
              className="mt-1.5 uppercase"
              onChange={(e) =>
                setDraft({ ...draft, code: e.target.value.toUpperCase() })
              }
            />
          </div>
          <div>
            <Label>Kind</Label>
            <Select
              value={draft.kind}
              onValueChange={(kind) =>
                setDraft({ ...draft, kind: kind as Promo["kind"] })
              }
            >
              <SelectTrigger className="mt-1.5">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="percent">Percent off</SelectItem>
                <SelectItem value="amount">Amount off</SelectItem>
                <SelectItem value="bonus_credits">Bonus credits</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="promo-value">
              {draft.kind === "percent"
                ? "Percent"
                : draft.kind === "amount"
                  ? `Amount (${draft.currency})`
                  : "Credits"}
            </Label>
            <Input
              id="promo-value"
              type="number"
              min={1}
              value={draft.value}
              className="mt-1.5"
              onChange={(e) => setDraft({ ...draft, value: e.target.value })}
            />
          </div>
          {draft.kind === "amount" ? (
            <div>
              <Label>Currency</Label>
              <Select
                value={draft.currency}
                onValueChange={(currency) => setDraft({ ...draft, currency })}
              >
                <SelectTrigger className="mt-1.5">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="INR">INR</SelectItem>
                  <SelectItem value="USD">USD</SelectItem>
                </SelectContent>
              </Select>
            </div>
          ) : null}
          <div>
            <Label htmlFor="promo-target">Applies to</Label>
            <Input
              id="promo-target"
              value={draft.applies_to}
              placeholder="any · any_plan · any_pack · plan:business · pack:p4999"
              className="mt-1.5"
              onChange={(e) =>
                setDraft({ ...draft, applies_to: e.target.value })
              }
            />
          </div>
          <div>
            <Label htmlFor="promo-until">Valid until (IST)</Label>
            <Input
              id="promo-until"
              type="date"
              value={draft.valid_until}
              className="mt-1.5"
              onChange={(e) =>
                setDraft({ ...draft, valid_until: e.target.value })
              }
            />
          </div>
          <div>
            <Label htmlFor="promo-max">Max uses (blank = unlimited)</Label>
            <Input
              id="promo-max"
              type="number"
              min={1}
              value={draft.max_redemptions}
              className="mt-1.5"
              onChange={(e) =>
                setDraft({ ...draft, max_redemptions: e.target.value })
              }
            />
          </div>
          <div>
            <Label htmlFor="promo-per">Uses per account</Label>
            <Input
              id="promo-per"
              type="number"
              min={1}
              value={draft.max_per_account}
              className="mt-1.5"
              onChange={(e) =>
                setDraft({ ...draft, max_per_account: e.target.value })
              }
            />
          </div>
          <div className="sm:col-span-2">
            <Label htmlFor="promo-note">Note</Label>
            <Input
              id="promo-note"
              value={draft.note}
              placeholder="Why this code exists"
              className="mt-1.5"
              onChange={(e) => setDraft({ ...draft, note: e.target.value })}
            />
          </div>
          <label className="flex items-center gap-2 self-end text-sm">
            <input
              type="checkbox"
              checked={draft.first_payment_only}
              onChange={(e) =>
                setDraft({ ...draft, first_payment_only: e.target.checked })
              }
            />
            First payment only
          </label>
        </div>
        <Button
          type="button"
          className="mt-4"
          disabled={saving || !draft.code.trim() || !draft.value}
          onClick={() => void create()}
        >
          {saving ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Plus className="mr-2 h-4 w-4" />
          )}
          Create code
        </Button>
      </section>

      <section className="rounded-xl border bg-card p-5">
        <h2 className="text-base font-medium">Codes</h2>
        {rows === null ? (
          <p className="mt-3 text-sm text-muted-foreground">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">No codes yet.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Code</TableHead>
                  <TableHead>Worth</TableHead>
                  <TableHead>Applies to</TableHead>
                  <TableHead>Until</TableHead>
                  <TableHead className="text-right">Uses</TableHead>
                  <TableHead className="text-right">Discounted</TableHead>
                  <TableHead className="text-right">Bonus credits</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((promo) => (
                  <TableRow key={promo.id}>
                    <TableCell className="font-mono font-medium">
                      {promo.code}
                      {promo.note ? (
                        <div className="text-xs font-sans font-normal text-muted-foreground">
                          {promo.note}
                        </div>
                      ) : null}
                    </TableCell>
                    <TableCell>{worth(promo)}</TableCell>
                    <TableCell>
                      {target(promo.applies_to)}
                      {promo.first_payment_only ? (
                        <div className="text-xs text-muted-foreground">
                          First payment only
                        </div>
                      ) : null}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {promo.valid_until
                        ? new Date(promo.valid_until).toLocaleDateString(
                            "en-IN",
                          )
                        : "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {promo.redemptions}
                      {promo.max_redemptions
                        ? ` / ${promo.max_redemptions}`
                        : ""}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatMinor(
                        promo.discount_minor_total,
                        promo.currency ?? "INR",
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {promo.bonus_credits_total.toLocaleString("en-IN")}
                    </TableCell>
                    <TableCell>
                      <span
                        className={
                          promo.active
                            ? "rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                            : "rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
                        }
                      >
                        {promo.active ? "Active" : "Revoked"}
                      </span>
                    </TableCell>
                    <TableCell className="text-right">
                      {promo.active ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={revoking === promo.id}
                          onClick={() => void revoke(promo)}
                        >
                          Revoke
                        </Button>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </section>
    </div>
  );
}
