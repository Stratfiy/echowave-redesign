"use client";

import { FeatureGate } from "@/components/helpers/FeatureGate";
import { WhoOwesMe } from "@/components/helpers/WhoOwesMe";
import { PageHeader } from "@/components/layout/PageHeader";

/** Who owes me (Follow-up; launch stream `agents`, `follow_up_ledger`). */
export default function FollowUpsPage() {
    return (
        <>
            <PageHeader title="Who owes me" description="Commitments you approved tracking. Each follow-up is a card you confirm." />
            <FeatureGate feature="follow_up_ledger">
                <WhoOwesMe />
            </FeatureGate>
        </>
    );
}
