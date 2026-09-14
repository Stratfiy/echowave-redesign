"use client";

/**
 * Invite a business (KAN-133).
 *
 * The account's referral link, a copy button, a WhatsApp share, and the
 * businesses that came through it with whether each has paid. Nothing is
 * earned on a signup; 200 credits land on each side when the friend's first
 * payment does, which is why the list says "signed up" and "paid" rather
 * than counting invitations.
 */

import { Copy, Gift, MessageCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { getReferralsApiV1ReferralsGet } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuth } from "@/lib/auth";
import { formatDateTimeIST } from "@/lib/billing/format";

type ReferredAccount = {
  name: string;
  referred_at: string | null;
  status: "signed_up" | "paid";
  rewarded: boolean;
};

type Referrals = {
  enabled: boolean;
  code: string;
  link: string;
  reward_credits: number;
  monthly_cap: number;
  this_month: number;
  earned_credits: number;
  accounts: ReferredAccount[];
};

function shareText(link: string, credits: number): string {
  return (
    `I use Decibyl to run bots for my business — phone, WhatsApp and routines. ` +
    `Sign up with my link and we both get ${credits.toLocaleString("en-IN")} free credits on your first payment: ${link}`
  );
}

export function InviteCard({ compact = false }: { compact?: boolean }) {
  const { user, loading: authLoading } = useAuth();
  const [referrals, setReferrals] = useState<Referrals | null>(null);

  useEffect(() => {
    if (authLoading || !user) return;
    let cancelled = false;
    (async () => {
      const response = await getReferralsApiV1ReferralsGet({});
      if (cancelled || response.error || !response.data) return;
      setReferrals(response.data as unknown as Referrals);
    })();
    return () => {
      cancelled = true;
    };
  }, [authLoading, user]);

  if (!referrals || !referrals.enabled) return null;

  const credits = referrals.reward_credits.toLocaleString("en-IN");
  const paid = referrals.accounts.filter((a) => a.status === "paid").length;

  return (
    <Card data-testid="invite-card">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <Gift className="h-4 w-4 text-primary" />
              Invite a business, earn {credits} credits
            </CardTitle>
            <CardDescription className="mt-1">
              They get {credits} credits too, on their first payment. Share your
              link on WhatsApp or paste it anywhere.
            </CardDescription>
          </div>
          {referrals.earned_credits > 0 ? (
            <span className="rounded-full border border-border bg-muted/40 px-2.5 py-1 text-xs tabular-nums text-muted-foreground">
              {referrals.earned_credits.toLocaleString("en-IN")} earned
            </span>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <code
            className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded-md border bg-muted px-3 py-2 text-xs"
            data-testid="invite-link"
          >
            {referrals.link}
          </code>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void navigator.clipboard.writeText(referrals.link);
              toast.success("Link copied");
            }}
          >
            <Copy className="mr-2 h-3.5 w-3.5" />
            Copy
          </Button>
          <Button asChild size="sm">
            <a
              href={`https://wa.me/?text=${encodeURIComponent(shareText(referrals.link, referrals.reward_credits))}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              <MessageCircle className="mr-2 h-3.5 w-3.5" />
              Share on WhatsApp
            </a>
          </Button>
        </div>
        {!compact && referrals.accounts.length > 0 ? (
          <ul className="divide-y divide-border rounded-md border border-border text-sm">
            {referrals.accounts.map((account, index) => (
              <li
                key={`${account.name}-${index}`}
                className="flex items-center justify-between gap-3 px-3 py-2"
              >
                <span className="min-w-0 truncate">{account.name}</span>
                <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                  {account.referred_at
                    ? formatDateTimeIST(account.referred_at)
                    : null}
                  <span
                    className={
                      account.status === "paid"
                        ? "rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                        : "rounded-full bg-muted px-2 py-0.5"
                    }
                  >
                    {account.status === "paid" ? "Paid" : "Signed up"}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        <p className="text-xs text-muted-foreground">
          {paid > 0
            ? `${paid} of ${referrals.accounts.length} referred businesses have paid. `
            : referrals.accounts.length > 0
              ? `${referrals.accounts.length} signed up, none paid yet. `
              : ""}
          Up to {referrals.monthly_cap} paid referrals a month
          {referrals.this_month > 0
            ? ` (${referrals.this_month} this month)`
            : ""}
          .
        </p>
      </CardContent>
    </Card>
  );
}
