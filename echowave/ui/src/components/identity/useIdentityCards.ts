"use client";

/**
 * The person's own identity cards, kept fresh while one is moving: polled
 * with backoff while any card is armed, running or waiting on an answer,
 * and left alone otherwise (design: "polling fallback uses backoff").
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { myIdentityCardsApiV1MeIdentityCardsGet } from "@/client/sdk.gen";
import type { IdentityCard } from "@/client/types.gen";
import { useAuth } from "@/lib/auth";

const MOVING = new Set(["armed", "running"]);

export function useIdentityCards(actions: readonly string[]) {
    const { user, loading: authLoading } = useAuth();
    const [cards, setCards] = useState<IdentityCard[]>([]);
    const [failed, setFailed] = useState(false);
    const delay = useRef(2000);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const key = actions.join(",");

    const load = useCallback(async () => {
        const res = await myIdentityCardsApiV1MeIdentityCardsGet();
        if (res.error || !res.data) {
            setFailed(true);
            return [] as IdentityCard[];
        }
        setFailed(false);
        const wanted = key.split(",");
        const mine = res.data.cards.filter((card) => wanted.includes(card.action));
        setCards(mine);
        return mine;
    }, [key]);

    const refresh = useCallback(async () => {
        if (timer.current) clearTimeout(timer.current);
        delay.current = 2000;
        const tick = async () => {
            const latest = await load();
            if (latest.some((card) => MOVING.has(card.state))) {
                timer.current = setTimeout(() => void tick(), delay.current);
                delay.current = Math.min(delay.current * 2, 30_000);
            }
        };
        await tick();
    }, [load]);

    useEffect(() => {
        if (authLoading || !user) return;
        void refresh();
        return () => {
            if (timer.current) clearTimeout(timer.current);
        };
    }, [authLoading, user, refresh]);

    return { cards, failed, refresh };
}
