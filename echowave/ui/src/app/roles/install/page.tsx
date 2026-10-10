"use client";

import { Loader2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import {
    installSharedRoleApiV1WorkspaceRolesSharedTokenInstallPost,
    previewSharedRoleApiV1WorkspaceRolesSharedTokenGet,
} from "@/client/sdk.gen";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { describeNeeds, type RoleNeed } from "@/lib/workspaceRoles";

interface Preview {
    name: string;
    summary: string | null;
    steps: number;
    needs: RoleNeed[];
}

/**
 * A shared role's link lands here (MP-3). It shows what the role is and
 * what this workspace would have to connect -- never its prompts, which
 * arrive only with the copy -- and installs it into the workspace the
 * person is signed in to.
 */
function InstallSharedRole() {
    const router = useRouter();
    const token = useSearchParams()?.get("token") ?? "";
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);
    const [preview, setPreview] = useState<Preview | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [installing, setInstalling] = useState(false);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void (async () => {
            if (!token) {
                setError("This link has no role in it.");
                return;
            }
            const result = await previewSharedRoleApiV1WorkspaceRolesSharedTokenGet({ path: { token } });
            if (result.error) {
                setError(detailFromResult(result, "That link could not be opened"));
                return;
            }
            setPreview(result.data as Preview);
        })();
    }, [authLoading, user, token]);

    const install = async () => {
        setInstalling(true);
        const result = await installSharedRoleApiV1WorkspaceRolesSharedTokenInstallPost({ path: { token } });
        setInstalling(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not add that role"));
            return;
        }
        router.push("/marketplace");
    };

    return (
        <>
            <PageHeader title="A shared role" />
            <PageBody className="mx-auto max-w-xl">
                {error ? (
                    <p role="alert" className="text-sm text-destructive">
                        {error}
                    </p>
                ) : !preview ? (
                    <p className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" /> Opening the link…
                    </p>
                ) : (
                    <Card>
                        <CardHeader>
                            <CardTitle>{preview.name}</CardTitle>
                            {preview.summary && <CardDescription>{preview.summary}</CardDescription>}
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <p className="text-sm text-muted-foreground">
                                {preview.steps === 1 ? "1 step" : `${preview.steps} steps`}. It is added to this
                                workspace&apos;s roles, and you add it from there.
                            </p>
                            {preview.needs.length > 0 && (
                                <div className="rounded-md bg-muted/50 p-3 text-sm">
                                    <p className="font-medium">You&apos;ll connect your own</p>
                                    <ul className="list-disc pl-5 text-muted-foreground">
                                        {describeNeeds(preview.needs).map((line) => (
                                            <li key={line}>{line}</li>
                                        ))}
                                    </ul>
                                </div>
                            )}
                            <Button disabled={installing} onClick={() => void install()}>
                                {installing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Add to my workspace
                            </Button>
                        </CardContent>
                    </Card>
                )}
            </PageBody>
        </>
    );
}

export default function InstallSharedRolePage() {
    return (
        <Suspense fallback={null}>
            <InstallSharedRole />
        </Suspense>
    );
}
