"use client";

import { useEffect, useState } from "react";

import { teamStatusApiV1TeamStatusGet } from "@/client/sdk.gen";
import type { TeamMember } from "@/client/types.gen";
import { useAuth } from "@/lib/auth";

export type RailData = {
  colleagues: TeamMember[];
};

const EMPTY: RailData = { colleagues: [] };

/**
 * Everything the v2 rail shows beyond links: the roster. One read the app
 * already makes elsewhere (the old rail's roster), fired once auth is ready.
 * A failed read hides its section, never the rail.
 *
 * There used to be a trial window and a credit balance here too. Nothing is
 * charged and there is no trial (founder, 9 Oct 2026), so both reads went.
 */
export function useRailData(): RailData {
  const { user, loading: authLoading } = useAuth();
  const [data, setData] = useState<RailData>(EMPTY);

  useEffect(() => {
    if (authLoading || !user) return;
    let cancelled = false;

    teamStatusApiV1TeamStatusGet({ query: { hours: 24 } })
      .then((response) => {
        if (!cancelled) setData({ colleagues: response.data?.members ?? [] });
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
    };
  }, [authLoading, user]);

  return data;
}
