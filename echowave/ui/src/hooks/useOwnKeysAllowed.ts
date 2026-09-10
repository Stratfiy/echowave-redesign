"use client";

/**
 * Whether this account may bring its own vendor keys.
 *
 * Off for every account until staff switch it on. Read from the account's
 * preferences, which the customer cannot change for this field; the tabs
 * and the Provider keys screen hide behind it so nobody is invited to paste
 * a key the server will refuse. `null` while unknown, so a screen can wait
 * rather than flash the wrong state.
 */

import { useEffect, useRef, useState } from "react";

import { getPreferencesApiV1OrganizationsPreferencesGet } from "@/client/sdk.gen";
import { useAuth } from "@/lib/auth";

export function useOwnKeysAllowed(): boolean | null {
    const { user, loading } = useAuth();
    const hasFetched = useRef(false);
    const [allowed, setAllowed] = useState<boolean | null>(null);

    useEffect(() => {
        if (loading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void (async () => {
            const response = await getPreferencesApiV1OrganizationsPreferencesGet();
            const body = response.data as { own_keys_allowed?: boolean } | undefined;
            setAllowed(Boolean(body?.own_keys_allowed));
        })();
    }, [loading, user]);

    return allowed;
}
