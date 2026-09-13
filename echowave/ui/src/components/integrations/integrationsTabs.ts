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

import type { PageTab } from "@/components/layout/PageHeader";
import { useOwnKeysAllowed } from "@/hooks/useOwnKeysAllowed";

export function useIntegrationsTabs(): PageTab[] {
  const ownKeysAllowed = useOwnKeysAllowed();

  return [
    // First, because it is the question people arrive with: what does this
    // connect to. The two behind it are where you then set one up.
    { href: "/integrations/apps", label: "Apps", prefix: true },
    // Exact match, or the Apps sub-route would light both it and Providers.
    ...(ownKeysAllowed ? [{ href: "/integrations", label: "Providers" }] : []),
    { href: "/tools", label: "Tools", prefix: true },
  ];
}
