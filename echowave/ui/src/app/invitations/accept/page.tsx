"use client";

/**
 * Where an invitation link lands.
 *
 * Shows what is being offered before it is taken, because a link out of an
 * email is exactly the thing people are right to be wary of clicking: the
 * organization, the role, and the address it was sent to, all before anything
 * is accepted.
 *
 * The address matters most. An invitation is granted to a mailbox and the
 * server refuses to hand the seat to anybody else, so somebody signed in as
 * the wrong account needs to be told that in words rather than left with a
 * button that fails.
 */

import { AlertTriangle, CheckCircle2, Loader2, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import {
    acceptInvitationApiV1OrganizationsInvitationsAcceptPost,
    previewInvitationApiV1OrganizationsInvitationsPreviewGet,
} from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

type Preview = {
    email: string;
    role: string;
    organization_name: string;
    expires_at: string;
};

const ROLE_LABELS: Record<string, string> = {
    owner: "Owner",
    admin: "Admin",
    member: "Member",
};

function AcceptInvitation() {
    const params = useSearchParams();
    const router = useRouter();
    const token = params.get("token") ?? "";
    const { user, loading: authLoading } = useAuth();

    const [preview, setPreview] = useState<Preview | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [accepting, setAccepting] = useState(false);
    const [accepted, setAccepted] = useState(false);

    useEffect(() => {
        if (authLoading || !user) return;
        if (!token) {
            setError("This link is missing its invitation code.");
            setLoading(false);
            return;
        }
        let cancelled = false;
        void (async () => {
            const response =
                await previewInvitationApiV1OrganizationsInvitationsPreviewGet({
                    query: { token },
                });
            if (cancelled) return;
            if (response.error) {
                setError(detailFromError(response.error, "This invitation is not valid."));
            } else {
                setPreview(response.data as Preview);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [token, authLoading, user]);

    const accept = async () => {
        setAccepting(true);
        const response = await acceptInvitationApiV1OrganizationsInvitationsAcceptPost({
            body: { token },
        });
        setAccepting(false);
        if (response.error) {
            setError(detailFromError(response.error, "Could not accept the invitation."));
            return;
        }
        setAccepted(true);
        // Full reload rather than a client push: the selected organization
        // changed server-side, and every cached screen behind this one is
        // still showing the previous account.
        window.location.href = "/overview";
    };

    if (authLoading || (loading && user)) {
        return <Centred><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></Centred>;
    }

    if (!user) {
        return (
            <Centred>
                <ShieldCheck className="mx-auto mb-4 h-10 w-10 text-muted-foreground" />
                <h1 className="text-lg font-semibold">Sign in to accept</h1>
                <p className="mt-2 text-sm text-muted-foreground">
                    This invitation was sent to a specific address. Sign in as that
                    address and open the link again.
                </p>
                <Button
                    className="mt-6"
                    onClick={() =>
                        router.push(
                            `/login?next=${encodeURIComponent(`/invitations/accept?token=${token}`)}`,
                        )
                    }
                >
                    Sign in
                </Button>
            </Centred>
        );
    }

    if (error) {
        return (
            <Centred>
                <AlertTriangle className="mx-auto mb-4 h-10 w-10 text-muted-foreground" />
                <h1 className="text-lg font-semibold">This invitation cannot be used</h1>
                <p className="mt-2 text-sm text-muted-foreground">{error}</p>
                <Button asChild variant="outline" className="mt-6">
                    <Link href="/overview">Back to your dashboard</Link>
                </Button>
            </Centred>
        );
    }

    if (accepted) {
        return (
            <Centred>
                <CheckCircle2 className="mx-auto mb-4 h-10 w-10 text-emerald-600 dark:text-emerald-400" />
                <h1 className="text-lg font-semibold">You are in</h1>
                <p className="mt-2 text-sm text-muted-foreground">Taking you there…</p>
            </Centred>
        );
    }

    const wrongAccount =
        preview != null &&
        (user as { primaryEmail?: string; email?: string }).email !== undefined &&
        (user as { email?: string }).email?.toLowerCase() !== preview.email.toLowerCase();

    return (
        <Centred>
            <h1 className="text-lg font-semibold">
                Join {preview?.organization_name}
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
                You have been invited as{" "}
                <span className="font-medium text-foreground">
                    {ROLE_LABELS[preview?.role ?? ""] ?? preview?.role}
                </span>
                , at {preview?.email}.
            </p>

            {wrongAccount && (
                // Said before the button rather than after a failed press: the
                // server grants the seat only to the invited address, so this
                // is the whole outcome, known in advance.
                <p className="mt-4 flex items-start gap-2 rounded-md bg-muted px-3 py-2 text-left text-sm text-muted-foreground">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>
                        You are signed in as a different address. Sign in as{" "}
                        {preview?.email} to accept this invitation.
                    </span>
                </p>
            )}

            <Button
                className="mt-6"
                onClick={() => void accept()}
                disabled={accepting || wrongAccount}
            >
                {accepting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Accept invitation
            </Button>
        </Centred>
    );
}

function Centred({ children }: { children: React.ReactNode }) {
    return (
        <div className="flex min-h-[70vh] items-center justify-center p-6">
            <Card className="w-full max-w-md">
                <CardContent className="p-8 text-center">{children}</CardContent>
            </Card>
        </div>
    );
}

export default function AcceptInvitationPage() {
    return (
        <Suspense
            fallback={
                <Centred>
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </Centred>
            }
        >
            <AcceptInvitation />
        </Suspense>
    );
}
