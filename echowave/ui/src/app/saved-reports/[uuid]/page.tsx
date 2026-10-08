"use client";

import { use } from "react";

import { FeatureGate } from "@/components/helpers/FeatureGate";
import { ReportView } from "@/components/helpers/ReportView";

/** One saved report at a stable address (screen 04, launch stream `agents`). */
export default function ReportPage({ params }: { params: Promise<{ uuid: string }> }) {
    const { uuid } = use(params);
    return (
        <FeatureGate feature="research_reports">
            <ReportView uuid={uuid} />
        </FeatureGate>
    );
}
