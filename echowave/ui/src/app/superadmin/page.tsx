"use client";

import { ArrowRight, KeyRound, List, Loader2, PhoneCall, ShieldCheck, Speech, Wallet } from 'lucide-react';
import Link from "next/link";
import { useState } from "react";

import { ErrorBanner } from "@/components/ErrorBanner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from '@/lib/auth';
import { impersonateAsSuperadmin } from "@/lib/utils";

type ImpersonationTarget = "provider" | "email";

export default function SuperadminPage() {
    const [providerUserId, setProviderUserId] = useState("");
    const [email, setEmail] = useState("");
    const [error, setError] = useState<{ target: ImpersonationTarget; message: string } | null>(null);
    const [loadingTarget, setLoadingTarget] = useState<ImpersonationTarget | null>(null);
    const { user, getAccessToken } = useAuth();

    const handleImpersonate = async (target: ImpersonationTarget, value: string) => {
        const trimmedValue = value.trim();
        setError(null);

        if (!trimmedValue) {
            setError({
                target,
                message: target === "provider" ? "Enter a provider user ID." : "Enter an email address.",
            });
            return;
        }

        setLoadingTarget(target);

        try {
            if (!user) {
                setError({
                    target,
                    message: "User not authenticated. Please log in and try again.",
                });
                return;
            }

            const accessToken = await getAccessToken();
            if (!accessToken) {
                throw new Error('Missing admin access token');
            }

            await impersonateAsSuperadmin({
                accessToken: accessToken,
                ...(target === "provider"
                    ? { providerUserId: trimmedValue }
                    : { email: trimmedValue }),
                redirectPath: '/workflow',
                openInNewTab: true,
            });
        } catch (err) {
            setError({
                target,
                message: err instanceof Error ? err.message : "Failed to impersonate user. Please try again.",
            });
            console.error("Impersonation error:", err);
        } finally {
            setLoadingTarget(null);
        }
    };

    const handleProviderImpersonate = async (e: React.FormEvent) => {
        e.preventDefault();
        await handleImpersonate("provider", providerUserId);
    };

    const handleEmailImpersonate = async (e: React.FormEvent) => {
        e.preventDefault();
        await handleImpersonate("email", email);
    };

    return (
        <>
            <main className="container mx-auto p-6 space-y-6 max-w-5xl">
                {/* Left-aligned like every other screen. This was the only
                    centred page title in the product, which read as a
                    different application rather than a different area. */}
                <div>
                    <h1 className="mb-2 text-[26px] leading-tight">Review queue</h1>
                    <p className="text-sm text-muted-foreground">Manage users and view system-wide data</p>
                </div>

                <div className="grid gap-6 md:grid-cols-2">
                        <Card>
                            <CardHeader>
                                <CardTitle>Provider User ID</CardTitle>
                                <CardDescription>
                                    Impersonate with the Stack provider user ID
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <form onSubmit={handleProviderImpersonate} className="space-y-4">
                                    <div className="space-y-2">
                                        <Label htmlFor="providerUserId">Provider User ID</Label>
                                        <Input
                                            id="providerUserId"
                                            value={providerUserId}
                                            onChange={(e) => setProviderUserId(e.target.value)}
                                            placeholder="Provider user ID"
                                            required
                                        />
                                    </div>

                                    {error?.target === "provider" && (
                                        <ErrorBanner>
                                            {error.message}
                                        </ErrorBanner>
                                    )}

                                    <Button
                                        type="submit"
                                        disabled={loadingTarget !== null}
                                        className="w-full"
                                    >
                                        {loadingTarget === "provider" ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                Processing...
                                            </>
                                        ) : (
                                            'Impersonate by Provider ID'
                                        )}
                                    </Button>
                                </form>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader>
                                <CardTitle>Email</CardTitle>
                                <CardDescription>
                                    Impersonate with a primary email address
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <form onSubmit={handleEmailImpersonate} className="space-y-4">
                                    <div className="space-y-2">
                                        <Label htmlFor="email">Email Address</Label>
                                        <Input
                                            id="email"
                                            type="email"
                                            value={email}
                                            onChange={(e) => setEmail(e.target.value)}
                                            placeholder="user@example.com"
                                            required
                                        />
                                    </div>

                                    {error?.target === "email" && (
                                        <ErrorBanner>
                                            {error.message}
                                        </ErrorBanner>
                                    )}

                                    <Button
                                        type="submit"
                                        disabled={loadingTarget !== null}
                                        className="w-full"
                                    >
                                        {loadingTarget === "email" ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                Processing...
                                            </>
                                        ) : (
                                            'Impersonate by Email'
                                        )}
                                    </Button>
                                </form>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader>
                                <CardTitle>Telephony Verification</CardTitle>
                                <CardDescription>
                                    Review customer KYC documents and forward them to the
                                    licensed operator
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <Link href="/superadmin/verification">
                                    <Button className="w-full md:w-auto">
                                        <ShieldCheck className="mr-2 h-4 w-4" />
                                        Open Review Queue
                                        <ArrowRight className="ml-2 h-4 w-4" />
                                    </Button>
                                </Link>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader>
                                <CardTitle>Provider Keys</CardTitle>
                                <CardDescription>
                                    Our own LLM, STT and TTS keys — what makes an
                                    account managed rather than bring-your-own
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <Link href="/superadmin/provider-keys">
                                    <Button className="w-full md:w-auto">
                                        <KeyRound className="mr-2 h-4 w-4" />
                                        Manage Keys
                                        <ArrowRight className="ml-2 h-4 w-4" />
                                    </Button>
                                </Link>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader>
                                <CardTitle>Workflow Runs</CardTitle>
                                <CardDescription>
                                    View and manage all workflow runs across organizations
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <Link href="/superadmin/runs">
                                    <Button className="w-full md:w-auto">
                                        <List className="mr-2 h-4 w-4" />
                                        View All Runs
                                        <ArrowRight className="ml-2 h-4 w-4" />
                                    </Button>
                                </Link>
                            </CardContent>
                        </Card>

                        {/* Built, routed and reachable — but nothing linked to it, so the
                            readiness checks and per-account balances it shows were
                            invisible unless you already knew the path. */}
                        <Card>
                            <CardHeader>
                                <CardTitle>Billing</CardTitle>
                                <CardDescription>
                                    Account balances, unit economics and the billing
                                    readiness checks
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <Link href="/superadmin/billing">
                                    <Button className="w-full md:w-auto">
                                        <Wallet className="mr-2 h-4 w-4" />
                                        Open Billing
                                        <ArrowRight className="ml-2 h-4 w-4" />
                                    </Button>
                                </Link>
                            </CardContent>
                        </Card>
                        {/* The switch that takes the shelf from empty to listed. A
                            role that makes or takes calls cannot be published
                            without a way to be heard, so with nothing set here every
                            calling role stays unlisted — and an operator with no link
                            to this screen has no way to find that out. */}
                        <Card>
                            <CardHeader>
                                <CardTitle>Demo agent</CardTitle>
                                <CardDescription>
                                    The agent a prospect hears before hiring a role.
                                    Until one is set, every calling role stays unlisted.
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <Link href="/superadmin/telephony/demo-agent">
                                    <Button className="w-full md:w-auto">
                                        <Speech className="mr-2 h-4 w-4" />
                                        Set the demo agent
                                        <ArrowRight className="ml-2 h-4 w-4" />
                                    </Button>
                                </Link>
                            </CardContent>
                        </Card>

                        {/* Same gap as Billing had: built, routed and reachable, with
                            nothing linking to it. The pool size here is the limit that
                            bites — a third simultaneous trial call waits for one of
                            these to free up — and it was invisible unless you already
                            knew the path. */}
                        <Card>
                            <CardHeader>
                                <CardTitle>Shared caller IDs</CardTitle>
                                <CardDescription>
                                    Decibyl&apos;s own numbers, lent to every account for
                                    outbound calls. The pool size is the trial-call limit.
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <Link href="/superadmin/telephony/shared-outbound">
                                    <Button className="w-full md:w-auto">
                                        <PhoneCall className="mr-2 h-4 w-4" />
                                        Open shared caller IDs
                                        <ArrowRight className="ml-2 h-4 w-4" />
                                    </Button>
                                </Link>
                            </CardContent>
                        </Card>
                </div>
            </main>
        </>
    );
}
