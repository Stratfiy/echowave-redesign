"use client";

/**
 * Buying a phone number, in the order the money says it has to happen.
 *
 * Documents → approved → search → select → **autopay** → number. The autopay
 * step sits between selecting and issuing on purpose, and it is the one that
 * looks out of place until you follow the money: a number is rent we owe a
 * carrier every month from the day we buy it. Issuing one before the customer
 * has authorised a standing instruction is a bill we pay and cannot collect
 * on. Asking for the mandate before showing them what is available is the
 * opposite mistake — nobody authorises a payment for a thing they have not
 * seen.
 *
 * The screen never claims a step is done on its own say-so. Verification is
 * the carrier's verdict and autopay is the bank's; both are read back from the
 * server after the fact, because a UI that marks itself complete is a UI that
 * disagrees with the thing that decides.
 */

import {
    AlertTriangle,
    CheckCircle2,
    CreditCard,
    ExternalLink,
    Loader2,
    RefreshCw,
    Search,
    ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
    createMandateApiV1BillingMandatePost,
    getKycApiV1KycGet,
    getMandateApiV1BillingMandateGet,
    listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet,
    provisionNumberApiV1ManagedNumbersPost,
    searchNumbersApiV1ManagedNumbersSearchPost,
} from "@/client/sdk.gen";
import { AgreementsDialog, useAgreements } from "@/components/AgreementsDialog";
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
import { Skeleton } from "@/components/ui/skeleton";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatPaise } from "@/lib/billing/format";
import { cn } from "@/lib/utils";

type AvailableNumber = {
    number: string;
    country_iso: string;
    number_type: string;
    city: string | null;
    region: string | null;
    monthly_rental: string | null;
    setup_price: string | null;
};

type Mandate = {
    id: number;
    status: string;
    authorised: boolean;
    authorisation_url: string | null;
    price_paise: number;
    authorised_at: string | null;
    last_failure_reason: string | null;
};

type MandateView = {
    mandate: Mandate | null;
    required_for_numbers: boolean;
    configured: boolean;
};

type ConfigOption = { id: number; name: string; is_platform_managed: boolean };

function Step({
    index,
    title,
    description,
    state,
    children,
}: {
    index: number;
    title: string;
    description?: string;
    state: "done" | "current" | "waiting";
    children?: React.ReactNode;
}) {
    return (
        <section
            className={cn(
                "glass-panel px-5 py-4",
                state === "waiting" && "opacity-55",
            )}
        >
            <div className="flex items-start gap-3">
                <span
                    className={cn(
                        "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
                        state === "done"
                            ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                            : state === "current"
                              ? "bg-foreground text-background"
                              : "bg-foreground/[0.08] text-muted-foreground",
                    )}
                >
                    {state === "done" ? (
                        <CheckCircle2 className="h-3.5 w-3.5" />
                    ) : (
                        index
                    )}
                </span>
                <div className="min-w-0 flex-1">
                    <h2 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                        {title}
                    </h2>
                    {description && (
                        <p className="mt-0.5 text-xs tracking-[-0.01em] text-muted-foreground">
                            {description}
                        </p>
                    )}
                    {children && <div className="mt-3">{children}</div>}
                </div>
            </div>
        </section>
    );
}

export default function BuyNumberPage() {
    const { user, loading: authLoading } = useAuth();
    const ready = !authLoading && Boolean(user);

    const [loading, setLoading] = useState(true);
    const [kycStatus, setKycStatus] = useState<string | null>(null);
    const [configs, setConfigs] = useState<ConfigOption[]>([]);
    const [configId, setConfigId] = useState<string>("");
    const [mandateView, setMandateView] = useState<MandateView | null>(null);

    const [city, setCity] = useState("");
    const [pattern, setPattern] = useState("");
    const [results, setResults] = useState<AvailableNumber[] | null>(null);
    const [searching, setSearching] = useState(false);
    const [selected, setSelected] = useState<string | null>(null);

    const [startingMandate, setStartingMandate] = useState(false);
    // `POST /api/v1/billing/mandate` requires ADMIN — a standing authority to
    // debit the account's bank account is not a member's to grant. Without
    // this, step 3 of the rental flow handed a member a button whose only
    // possible answer was "Could not start autopay", three steps into a
    // journey they cannot finish.
    const { isOrganizationAdmin, loaded: rolesLoaded } = useAccessRoles();
    const [buying, setBuying] = useState(false);
    const [bought, setBought] = useState<string | null>(null);

    // The terms are asked for at the buy step, not at sign-up. This is the
    // first moment they have teeth: rent every month from the customer, and a
    // carrier contract in their name from us. The server enforces the same
    // thing in `provisioning`, so a client that skipped this gets a 403 rather
    // than a number.
    const agreements = useAgreements(ready);
    const [agreementsOpen, setAgreementsOpen] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const refreshMandate = useCallback(async () => {
        const result = await getMandateApiV1BillingMandateGet();
        if (result.error) return;
        setMandateView(result.data as unknown as MandateView);
    }, []);

    useEffect(() => {
        if (!ready) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            // Verification first, and on its own. Reading it is what gives an
            // approved account its managed carrier configuration if it does not
            // have one yet, so fetching the configuration list alongside it
            // races that write and loses: the list comes back empty and the
            // screen says "No managed carrier account is set up for this
            // organisation yet. Contact support" to somebody who is fully
            // approved and one reload away from the truth.
            const kyc = await getKycApiV1KycGet();
            if (cancelled) return;

            const [configList, mandate] = await Promise.all([
                listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet(),
                getMandateApiV1BillingMandateGet(),
            ]);
            if (cancelled) return;

            if (!kyc.error) {
                setKycStatus(
                    (kyc.data as unknown as { status: string })?.status ?? null,
                );
            }
            if (!configList.error) {
                const rows =
                    (
                        configList.data as unknown as {
                            configurations: ConfigOption[];
                        }
                    )?.configurations ?? [];
                // Only configurations we administer can sell a number — the
                // provisioning route refuses the rest, and offering a choice
                // that is going to be refused is worse than not offering it.
                const managed = rows.filter((row) => row.is_platform_managed);
                setConfigs(managed);
                if (managed.length === 1) setConfigId(String(managed[0].id));
            }
            if (!mandate.error) {
                setMandateView(mandate.data as unknown as MandateView);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [ready]);

    const approved = kycStatus === "carrier_approved";
    const mandate = mandateView?.mandate ?? null;
    const mandateRequired = mandateView?.required_for_numbers !== false;
    const autopayDone = !mandateRequired || Boolean(mandate?.authorised);

    const handleSearch = async () => {
        if (!configId) return;
        setSearching(true);
        setError(null);
        const result = await searchNumbersApiV1ManagedNumbersSearchPost({
            body: {
                telephony_configuration_id: Number(configId),
                country_iso: "IN",
                number_type: "local",
                city: city || null,
                pattern: pattern || null,
                limit: 20,
            },
        });
        setSearching(false);
        if (result.error) {
            setError(detailFromError(result.error, "Could not search for numbers"));
            return;
        }
        const numbers =
            (result.data as unknown as { numbers: AvailableNumber[] })?.numbers ?? [];
        setResults(numbers);
        setSelected(null);
    };

    const handleStartAutopay = async () => {
        setStartingMandate(true);
        setError(null);
        const result = await createMandateApiV1BillingMandatePost();
        setStartingMandate(false);
        if (result.error) {
            setError(detailFromError(result.error, "Could not start autopay"));
            return;
        }
        const created = (result.data as unknown as { mandate: Mandate }).mandate;
        setMandateView((current) =>
            current ? { ...current, mandate: created } : current,
        );
        if (created?.authorisation_url) {
            // The provider's own hosted page. Opened in a new tab rather than
            // navigated to, so coming back does not lose the number they picked.
            window.open(created.authorisation_url, "_blank", "noopener");
        }
    };

    const handleBuy = () => {
        if (agreements.outstanding.length > 0) {
            setAgreementsOpen(true);
            return;
        }
        void doBuy();
    };

    const doBuy = async () => {
        if (!selected || !configId) return;
        setBuying(true);
        setError(null);
        const result = await provisionNumberApiV1ManagedNumbersPost({
            body: {
                telephony_configuration_id: Number(configId),
                address: selected,
                country_code: "IN",
            },
        });
        setBuying(false);
        if (result.error) {
            setError(detailFromError(result.error, "Could not buy that number"));
            return;
        }
        const issued = result.data as unknown as { address: string };
        setBought(issued.address);
        toast.success(`${issued.address} is yours`);
    };

    if (!ready || loading) {
        return (
            <div className="container mx-auto max-w-3xl space-y-4 p-6">
                <Skeleton className="h-24 w-full rounded-xl" />
                <Skeleton className="h-24 w-full rounded-xl" />
                <Skeleton className="h-24 w-full rounded-xl" />
            </div>
        );
    }

    if (bought) {
        return (
            <div className="container mx-auto max-w-3xl p-6">
                <div className="glass-panel px-6 py-8 text-center">
                    <CheckCircle2 className="mx-auto h-8 w-8 text-emerald-600 dark:text-emerald-400" />
                    <h1 className="mt-3 text-2xl font-semibold tracking-[-0.02em]">
                        {bought} is yours
                    </h1>
                    <p className="mt-2 text-sm text-muted-foreground">
                        Point an agent at it to start answering calls. Rent is
                        collected monthly by autopay.
                    </p>
                    <div className="mt-5 flex justify-center gap-2">
                        <Button asChild>
                            <Link href="/telephony-configurations">
                                Set up routing
                            </Link>
                        </Button>
                        <Button variant="outline" onClick={() => setBought(null)}>
                            Buy another
                        </Button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="container mx-auto max-w-3xl space-y-4 p-6">
            <div>
                <h1 className="mb-1 text-3xl font-bold">Get a phone number</h1>
                <p className="text-muted-foreground">
                    An Indian number of your own, ₹349 a month, on our carrier
                    account.
                </p>
            </div>

            {error && (
                <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                    <p>{error}</p>
                </div>
            )}

            <Step
                index={1}
                title="Telephony verification"
                description="A licensed operator has to approve your business before any Indian number can be issued."
                state={approved ? "done" : "current"}
            >
                {approved ? (
                    <p className="flex items-center gap-2 text-sm text-muted-foreground">
                        <ShieldCheck className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                        Approved by the carrier.
                    </p>
                ) : (
                    <div className="space-y-2">
                        <p className="text-sm text-muted-foreground">
                            {kycStatus
                                ? `Currently ${kycStatus.replace(/_/g, " ")}.`
                                : "Not started."}
                        </p>
                        <Button asChild size="sm">
                            <Link href="/verification">Go to verification</Link>
                        </Button>
                    </div>
                )}
            </Step>

            <Step
                index={2}
                title="Pick a number"
                description="India local inventory is thin and moves, so an empty result is a normal answer rather than an error."
                state={
                    !approved ? "waiting" : selected ? "done" : "current"
                }
            >
                {approved && (
                    <div className="space-y-3">
                        {configs.length === 0 ? (
                            <p className="text-sm text-muted-foreground">
                                No managed carrier account is set up for this
                                organisation yet. Contact support — this is ours to
                                configure, not yours.
                            </p>
                        ) : (
                            <>
                                <div className="grid gap-3 sm:grid-cols-3">
                                    {configs.length > 1 && (
                                        <div className="space-y-1.5">
                                            <Label>Carrier account</Label>
                                            <Select
                                                value={configId}
                                                onValueChange={setConfigId}
                                            >
                                                <SelectTrigger>
                                                    <SelectValue placeholder="Choose" />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {configs.map((config) => (
                                                        <SelectItem
                                                            key={config.id}
                                                            value={String(config.id)}
                                                        >
                                                            {config.name}
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    )}
                                    <div className="space-y-1.5">
                                        <Label htmlFor="city">City</Label>
                                        <Input
                                            id="city"
                                            value={city}
                                            placeholder="Hyderabad"
                                            onChange={(e) => setCity(e.target.value)}
                                        />
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label htmlFor="pattern">Contains</Label>
                                        <Input
                                            id="pattern"
                                            value={pattern}
                                            placeholder="7890"
                                            onChange={(e) => setPattern(e.target.value)}
                                        />
                                    </div>
                                </div>

                                <Button
                                    size="sm"
                                    onClick={handleSearch}
                                    disabled={searching || !configId}
                                >
                                    {searching ? (
                                        <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                                    ) : (
                                        <Search className="mr-2 h-3.5 w-3.5" />
                                    )}
                                    Search
                                </Button>

                                {results !== null && results.length === 0 && (
                                    <p className="text-sm text-muted-foreground">
                                        Nothing available matching that right now.
                                        Try a different city, or search again in a
                                        few minutes — inventory turns over.
                                    </p>
                                )}

                                {results !== null && results.length > 0 && (
                                    <ul className="space-y-2">
                                        {results.map((row) => (
                                            <li key={row.number}>
                                                <button
                                                    type="button"
                                                    onClick={() => setSelected(row.number)}
                                                    className={cn(
                                                        "glass-tile flex w-full items-center justify-between gap-3 px-4 py-3 text-left",
                                                        selected === row.number &&
                                                            "ring-2 ring-foreground/60",
                                                    )}
                                                >
                                                    <span>
                                                        <span className="block font-mono text-sm font-medium">
                                                            {row.number}
                                                        </span>
                                                        <span className="block text-xs text-muted-foreground">
                                                            {[row.city, row.region]
                                                                .filter(Boolean)
                                                                .join(", ") || "India"}
                                                        </span>
                                                    </span>
                                                    <span className="text-xs text-muted-foreground">
                                                        ₹349/month
                                                    </span>
                                                </button>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </>
                        )}
                    </div>
                )}
            </Step>

            <Step
                index={3}
                title="Set up autopay"
                description="The rent is monthly and starts the day the number is issued, so it is authorised before the number is handed over — not after."
                state={
                    !selected ? "waiting" : autopayDone ? "done" : "current"
                }
            >
                {selected && (
                    <div className="space-y-3">
                        {!mandateRequired ? (
                            <p className="text-sm text-muted-foreground">
                                Autopay is not required on this deployment. The
                                monthly rental will be taken from your prepaid
                                balance.
                            </p>
                        ) : mandate?.authorised ? (
                            <p className="flex items-center gap-2 text-sm text-muted-foreground">
                                <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                                Authorised — {formatPaise(mandate.price_paise)} a
                                month.
                            </p>
                        ) : mandateView?.configured === false ? (
                            <p className="text-sm text-muted-foreground">
                                Autopay is not configured on this deployment yet.
                                Contact support — this one is ours to fix.
                            </p>
                        ) : rolesLoaded && !isOrganizationAdmin ? (
                            // Named so the member knows the flow is not broken
                            // and knows exactly what to ask for. The steps
                            // above stay usable: they can still search and pick
                            // the number, so the request they make is specific.
                            <p className="flex items-start gap-2 text-sm text-muted-foreground">
                                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
                                <span>
                                    Autopay authorises a monthly debit for the whole
                                    account, so an Owner or Admin has to set it up. Your
                                    choice above is kept — ask one of them to authorise
                                    autopay, then come back and issue the number.
                                </span>
                            </p>
                        ) : (
                            <>
                                <div className="flex flex-wrap items-center gap-2">
                                    <Button
                                        size="sm"
                                        onClick={handleStartAutopay}
                                        disabled={startingMandate}
                                    >
                                        {startingMandate ? (
                                            <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                                        ) : (
                                            <CreditCard className="mr-2 h-3.5 w-3.5" />
                                        )}
                                        {mandate
                                            ? "Open the authorisation page"
                                            : "Set up autopay"}
                                    </Button>
                                    {mandate?.authorisation_url && (
                                        <Button variant="outline" size="sm" asChild>
                                            <a
                                                href={mandate.authorisation_url}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                            >
                                                <ExternalLink className="mr-2 h-3.5 w-3.5" />
                                                Authorisation page
                                            </a>
                                        </Button>
                                    )}
                                    {/* The bank tells us, not the browser. There
                                        is no "I've done it" button for the same
                                        reason there is no "I've paid" button —
                                        the only thing that can mark this done is
                                        the provider's own callback. */}
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={refreshMandate}
                                    >
                                        <RefreshCw className="mr-2 h-3.5 w-3.5" />
                                        Check again
                                    </Button>
                                </div>
                                {mandate && (
                                    <p className="text-xs text-muted-foreground">
                                        Status: {mandate.status.replace(/_/g, " ")}.
                                        Once your bank confirms it, this step
                                        completes on its own.
                                    </p>
                                )}
                                {mandate?.last_failure_reason && (
                                    <p className="text-xs text-destructive">
                                        {mandate.last_failure_reason}
                                    </p>
                                )}
                            </>
                        )}
                    </div>
                )}
            </Step>

            <Step
                index={4}
                title="Issue the number"
                description="Bought from the carrier and pointed at your account in one step."
                state={selected && autopayDone ? "current" : "waiting"}
            >
                {selected && autopayDone && (
                    <div className="space-y-2">
                        <p className="text-sm text-muted-foreground">
                            <span className="font-mono font-medium text-foreground">
                                {selected}
                            </span>{" "}
                            — ₹349 a month, charged from today, prorated for the
                            rest of this month.
                        </p>
                        <Button onClick={handleBuy} disabled={buying}>
                            {buying && (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            )}
                            Buy {selected}
                        </Button>
                        {agreements.outstanding.length > 0 && (
                            <p className="text-xs text-muted-foreground">
                                We will ask you to accept the{" "}
                                {agreements.agreements
                                    .filter((a) =>
                                        agreements.outstanding.includes(a.key),
                                    )
                                    .map((a) => a.title)
                                    .join(" and ")}{" "}
                                first.
                            </p>
                        )}
                    </div>
                )}
            </Step>

            <AgreementsDialog
                open={agreementsOpen}
                state={agreements}
                onOpenChange={setAgreementsOpen}
                onAccepted={() => void doBuy()}
                reason={
                    "Buying a number commits you to rent every month and commits us to a carrier contract in your name, so we need these before we can issue it."
                }
            />
        </div>
    );
}
