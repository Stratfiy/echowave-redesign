"use client";

/**
 * One place for everything that connects Decibyl to something outside it.
 *
 * Provider keys and Tools were two peers in the left navigation, and Google
 * Calendar lived inside the provider-keys screen with no name for what it
 * was doing there. All three are the same idea — an outside account or
 * service an agent can reach — which is why a competitor product groups them
 * under one "Integrations" label instead of three.
 *
 * A hook rather than a constant, because whether Providers is offered at all
 * depends on the deployment. The strip itself is `PageTabs`, rendered by
 * `PageHeader` beneath the page title, like every other section in the
 * product — this file used to carry a fourth private copy of that strip,
 * with its own active colour.
 */

import { useEffect, useState } from "react";

import { listDialerConnectionsApiV1DialerConnectionsGet } from "@/client/sdk.gen";
import type { PageTab } from "@/components/layout/PageHeader";
import { useOwnKeysAllowed } from "@/hooks/useOwnKeysAllowed";
import { useAuth } from "@/lib/auth";

/**
 * Whether the dialer import is switched on here (CR-2). The route is a 404
 * while it is off, which is the only signal the UI has; asked once per page
 * load and shared, so every tab strip does not ask again. A 403 (a member,
 * not an admin) still means it exists, so the tab shows and the screen
 * explains.
 */
let dialerAvailable: Promise<boolean> | null = null;

export function resetDialerAvailability(): void {
  dialerAvailable = null;
}

function useDialerAvailable(): boolean {
  const [available, setAvailable] = useState(false);
  // Asked only once auth has loaded: an unauthenticated request is refused,
  // and a refusal is not an answer to whether the feature exists.
  const { user, loading: authLoading } = useAuth();
  useEffect(() => {
    if (authLoading || !user) return;
    let live = true;
    dialerAvailable ??= listDialerConnectionsApiV1DialerConnectionsGet()
      .then((result) => result.response?.status !== 404)
      .catch(() => false);
    void dialerAvailable.then((value) => {
      if (live) setAvailable(value);
    });
    return () => {
      live = false;
    };
  }, [authLoading, user]);
  return available;
}

export function useIntegrationsTabs(): PageTab[] {
  const ownKeysAllowed = useOwnKeysAllowed();
  const dialer = useDialerAvailable();

  return [
    // The app catalogue is the Marketplace's Integrations tab now; what is
    // left here is what this account has rather than what it could add.
    { href: "/tools", label: "Your tools", prefix: true },
    // Exact match, or a sub-route would light this as well as itself.
    ...(ownKeysAllowed ? [{ href: "/integrations", label: "Providers" }] : []),
    ...(dialer ? [{ href: "/integrations/dialer", label: "Dialer" }] : []),
  ];
}
