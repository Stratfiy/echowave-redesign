"use client";

/**
 * Applying to be a partner.
 *
 * Four questions, not a second signup. A partner account is an ordinary
 * account with a commercial arrangement attached — everything the product does
 * it does the same way afterwards — so this screen exists to ask, and nothing
 * on it changes what the account can do.
 *
 * Open to any member on purpose. Asking is not spending, the answer is a staff
 * decision either way, and the person at an agency who notices there is a
 * partner programme is rarely the one holding the billing profile.
 */

import {
    AlertTriangle,
    CheckCircle2,
    Clock,
    Copy,
    Loader2,
    Users,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    getApplicationApiV1PartnersApplicationGet,
    getReferralsApiV1PartnersReferralsGet,
    getStatementsApiV1PartnersStatementsGet,
    submitApplicationApiV1PartnersApplicationPost,
} from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
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
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatPaise } from "@/lib/billing/format";

type Application = {
    id: number;
    kind: string;
    expected_minutes_per_month: number | null;
    status: string;
    submitted_at: string | null;
    decision_note: string | null;
};

type Commission = {
    commission_bps: number;
    basis: string;
};

/** An account that signed up through this partner's link. */
type ReferredAccount = {
    name: string;
    referred_at: string | null;
};

type Referrals = {
    referral_code: string | null;
    referral_link: string | null;
    accounts: ReferredAccount[];
};

type StatementLine = {
    account: string;
    basis_amount_paise: number;
    amount_paise: number;
};

/**
 * One period's earnings.
 *
 * Drafts never reach here — the server withholds them. A draft is our working
 * number, regenerated whenever a late rollup or a recost moves it, and showing
 * a partner a figure that changes under them is how a number becomes an
 * argument.
 */
type Statement = {
    id: number;
    period_start: string;
    period_end: string;
    basis: string;
    amount_paise: number;
    basis_amount_paise: number;
    status: string;
    paid_at: string | null;
    payment_reference: string | null;
    lines: StatementLine[];
};

const KIND_LABELS: Record<string, string> = {
    developer: "Developer — building on the API",
    agency: "Agency — running accounts for clients",
    reseller: "Reseller — selling Decibyl on",
};

const BASIS_LABELS: Record<string, string> = {
    platform_fee: "of the platform fee",
    total_spend: "of total spend",
};

export default function PartnerPage() {
    const { user, loading: authLoading } = useAuth();

    const [application, setApplication] = useState<Application | null>(null);
    const [commission, setCommission] = useState<Commission | null>(null);
    const [kinds, setKinds] = useState<string[]>([]);
    const [referrals, setReferrals] = useState<Referrals | null>(null);
    const [statements, setStatements] = useState<Statement[]>([]);
    const [loading, setLoading] = useState(true);

    const [kind, setKind] = useState("agency");
    const [minutes, setMinutes] = useState("");
    const [website, setWebsite] = useState("");
    const [note, setNote] = useState("");
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        if (authLoading || !user) return;
        let cancelled = false;
        void (async () => {
            const response = await getApplicationApiV1PartnersApplicationGet();
            if (cancelled) return;
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not load your application"));
            } else {
                const body = response.data as {
                    application: Application | null;
                    commission: Commission | null;
                    kinds: string[];
                };
                setApplication(body.application);
                setCommission(body.commission);
                setKinds(body.kinds ?? []);
                if (body.kinds?.length) setKind(body.kinds.includes("agency") ? "agency" : body.kinds[0]);

                // Only for an approved partner. Before that there is no code
                // to show and no period to have earned in, and two requests
                // that can only 400 are two requests worth not making.
                if (body.application?.status === "approved") {
                    const [referralsResponse, statementsResponse] = await Promise.all([
                        getReferralsApiV1PartnersReferralsGet(),
                        getStatementsApiV1PartnersStatementsGet(),
                    ]);
                    if (cancelled) return;
                    if (!referralsResponse.error) {
                        setReferrals(referralsResponse.data as unknown as Referrals);
                    }
                    if (!statementsResponse.error) {
                        setStatements(
                            (statementsResponse.data as unknown as {
                                statements?: Statement[];
                            })?.statements ?? [],
                        );
                    }
                }
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authLoading, user]);

    const submit = async () => {
        setSubmitting(true);
        const response = await submitApplicationApiV1PartnersApplicationPost({
            body: {
                kind,
                expected_minutes_per_month: minutes.trim() ? Number(minutes) : null,
                company_website: website.trim() || null,
                note: note.trim() || null,
            },
        });
        setSubmitting(false);
        if (response.error) {
            toast.error(detailFromError(response.error, "Could not submit your application"));
            return;
        }
        setApplication((response.data as { application: Application }).application);
        toast.success("Application submitted");
    };

    if (loading) {
        return (
            <div className="space-y-3 p-6">
                <Skeleton className="h-8 w-64" />
                <Skeleton className="h-40 w-full max-w-2xl" />
            </div>
        );
    }

    const pending = application?.status === "pending";
    const approved = application?.status === "approved";
    const rejected = application?.status === "rejected";

    return (
        <div className="mx-auto max-w-2xl space-y-6 p-6">
            <div>
                <h1 className="text-xl font-semibold">Partner programme</h1>
                <p className="mt-1 text-sm text-muted-foreground">
                    For developers building on the API, agencies running accounts for
                    clients, and resellers. Your account keeps working exactly as it
                    does now — this adds a commercial arrangement, not a different
                    product.
                </p>
            </div>

            {approved && commission && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base">
                            <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                            You are a partner
                        </CardTitle>
                        <CardDescription>
                            {/* The rate and its basis, and nothing else. What it is
                                computed against is our rate card, not theirs. */}
                            You earn {(commission.commission_bps / 100).toFixed(2)}%{" "}
                            {BASIS_LABELS[commission.basis] ?? commission.basis}.
                        </CardDescription>
                    </CardHeader>
                    {application?.decision_note && (
                        <CardContent className="pt-0 text-sm text-muted-foreground">
                            {application.decision_note}
                        </CardContent>
                    )}
                </Card>
            )}

            {/* The link is the only mechanism by which anything is attributed.
                A rate with nobody signing up through it earns nothing, so this
                sits directly under the rate rather than further down. */}
            {approved && referrals?.referral_link && (
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">Your referral link</CardTitle>
                        <CardDescription>
                            An account that signs up through this link is attributed to
                            you from that moment. Attribution happens once, at signup —
                            it cannot be added to an account afterwards, so send the
                            link before they sign up rather than after.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex flex-wrap items-center gap-2">
                            <code className="flex-1 overflow-x-auto whitespace-nowrap rounded-md border bg-muted px-3 py-2 text-xs">
                                {referrals.referral_link}
                            </code>
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                    void navigator.clipboard.writeText(
                                        referrals.referral_link ?? "",
                                    );
                                    toast.success("Link copied");
                                }}
                            >
                                <Copy className="mr-2 h-3.5 w-3.5" />
                                Copy
                            </Button>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Your code is{" "}
                            <span className="font-mono font-medium">
                                {referrals.referral_code}
                            </span>
                            . It never changes, so a link already printed on a slide
                            keeps working.
                        </p>

                        <div className="rounded-lg border p-3">
                            <p className="flex items-center gap-2 text-sm font-medium">
                                <Users className="h-4 w-4" />
                                {referrals.accounts.length === 0
                                    ? "No accounts yet"
                                    : `${referrals.accounts.length} account${
                                          referrals.accounts.length === 1 ? "" : "s"
                                      }`}
                            </p>
                            {referrals.accounts.length === 0 ? (
                                <p className="mt-1 text-xs text-muted-foreground">
                                    Accounts that sign up through your link appear here
                                    straight away, before they have spent anything.
                                </p>
                            ) : (
                                <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
                                    {referrals.accounts.map((account) => (
                                        <li
                                            key={`${account.name}-${account.referred_at}`}
                                            className="flex justify-between gap-4"
                                        >
                                            <span>{account.name}</span>
                                            <span className="text-xs tabular-nums">
                                                {account.referred_at
                                                    ? new Date(
                                                          account.referred_at,
                                                      ).toLocaleDateString()
                                                    : "—"}
                                            </span>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Statements. Each one carries its own breakdown, because "why is
                this number what it is" is the question a partner asks and a
                total on its own cannot answer. */}
            {approved && (
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">Earnings</CardTitle>
                        <CardDescription>
                            One statement per period, with what each account you
                            referred contributed.
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        {statements.length === 0 ? (
                            <p className="text-sm text-muted-foreground">
                                No statements yet. A period appears here once it has
                                been closed and issued — the accounts you referred can
                                already be spending before that happens.
                            </p>
                        ) : (
                            <div className="space-y-6">
                                {statements.map((statement) => (
                                    <div key={statement.id} className="space-y-2">
                                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                                            <p className="text-sm font-medium">
                                                {statement.period_start} to{" "}
                                                {statement.period_end}
                                            </p>
                                            <p className="text-sm tabular-nums">
                                                <span className="font-semibold">
                                                    {formatPaise(statement.amount_paise)}
                                                </span>{" "}
                                                <span className="text-muted-foreground">
                                                    {statement.status === "paid"
                                                        ? `paid${
                                                              statement.payment_reference
                                                                  ? ` · ${statement.payment_reference}`
                                                                  : ""
                                                          }`
                                                        : "due"}
                                                </span>
                                            </p>
                                        </div>
                                        <Table>
                                            <TableHeader>
                                                <TableRow>
                                                    <TableHead>Account</TableHead>
                                                    <TableHead className="text-right">
                                                        {BASIS_LABELS[statement.basis]
                                                            ? BASIS_LABELS[
                                                                  statement.basis
                                                              ].replace("of ", "")
                                                            : "Basis"}
                                                    </TableHead>
                                                    <TableHead className="text-right">
                                                        You earned
                                                    </TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {statement.lines.map((line) => (
                                                    <TableRow key={line.account}>
                                                        <TableCell>{line.account}</TableCell>
                                                        <TableCell className="text-right tabular-nums">
                                                            {formatPaise(
                                                                line.basis_amount_paise,
                                                            )}
                                                        </TableCell>
                                                        <TableCell className="text-right tabular-nums">
                                                            {formatPaise(line.amount_paise)}
                                                        </TableCell>
                                                    </TableRow>
                                                ))}
                                            </TableBody>
                                        </Table>
                                    </div>
                                ))}
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            {pending && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base">
                            <Clock className="h-4 w-4 text-muted-foreground" />
                            We are looking at your application
                        </CardTitle>
                        <CardDescription>
                            Submitted as {KIND_LABELS[application.kind] ?? application.kind}
                            {application.expected_minutes_per_month
                                ? `, ${application.expected_minutes_per_month.toLocaleString()} minutes a month`
                                : ""}
                            . We will email you when somebody has read it.
                        </CardDescription>
                    </CardHeader>
                </Card>
            )}

            {rejected && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base">
                            <AlertTriangle className="h-4 w-4 text-muted-foreground" />
                            Not this time
                        </CardTitle>
                        <CardDescription>
                            {application?.decision_note ??
                                "We could not take this one forward. You can apply again."}
                        </CardDescription>
                    </CardHeader>
                </Card>
            )}

            {!pending && !approved && (
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">
                            {rejected ? "Apply again" : "Apply"}
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="space-y-1.5">
                            <Label htmlFor="kind">What are you?</Label>
                            <Select value={kind} onValueChange={setKind}>
                                <SelectTrigger id="kind">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {kinds.map((k) => (
                                        <SelectItem key={k} value={k}>
                                            {KIND_LABELS[k] ?? k}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        <div className="space-y-1.5">
                            <Label htmlFor="minutes">
                                Expected minutes per month
                            </Label>
                            <Input
                                id="minutes"
                                type="number"
                                min={0}
                                value={minutes}
                                onChange={(e) => setMinutes(e.target.value)}
                                placeholder="5000"
                            />
                            <p className="text-xs text-muted-foreground">
                                {/* Says why it is asked, because a number asked for
                                    without a reason gets a made-up answer. */}
                                A rough figure is fine. It is what a commission is
                                quoted against.
                            </p>
                        </div>

                        <div className="space-y-1.5">
                            <Label htmlFor="website">Website</Label>
                            <Input
                                id="website"
                                value={website}
                                onChange={(e) => setWebsite(e.target.value)}
                                placeholder="https://"
                            />
                        </div>

                        <div className="space-y-1.5">
                            <Label htmlFor="note">Anything else</Label>
                            <Textarea
                                id="note"
                                value={note}
                                onChange={(e) => setNote(e.target.value)}
                                rows={4}
                                placeholder="Who your clients are, what you are replacing."
                            />
                        </div>

                        <Button onClick={() => void submit()} disabled={submitting}>
                            {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Submit application
                        </Button>
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
