"use client";

import { use } from "react";

import { FeatureGate } from "@/components/helpers/FeatureGate";
import { TrackerView } from "@/components/helpers/Trackers";

/** One tracker at a stable address (launch stream `agents`). */
export default function TrackerPage({ params }: { params: Promise<{ uuid: string }> }) {
    const { uuid } = use(params);
    return (
        <FeatureGate feature="describe_builder">
            <TrackerView uuid={uuid} />
        </FeatureGate>
    );
}
