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

import { AlertTriangle, CheckCircle2, Clock, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    getApplicationApiV1PartnersApplicationGet,
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
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

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
