"use client";

import { FeatureGate } from "@/components/helpers/FeatureGate";
import { TrackerList } from "@/components/helpers/Trackers";
import { PageHeader } from "@/components/layout/PageHeader";

/** Trackers the builder made (launch stream `agents`, `describe_builder`). */
export default function TrackersPage() {
    return (
        <>
            <PageHeader title="Trackers" description="Lists Decibyl built from your description." />
            <FeatureGate feature="describe_builder">
                <TrackerList />
            </FeatureGate>
        </>
    );
}
