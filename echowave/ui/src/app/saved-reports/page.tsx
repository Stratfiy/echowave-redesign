"use client";

import { FeatureGate } from "@/components/helpers/FeatureGate";
import { ReportList } from "@/components/helpers/ReportView";
import { PageHeader } from "@/components/layout/PageHeader";

/** Saved research reports (launch stream `agents`, `research_reports`). */
export default function ReportsPage() {
    return (
        <>
            <PageHeader title="Saved reports" description="Research you kept, with its sources. Private unless you share it." />
            <FeatureGate feature="research_reports">
                <ReportList />
            </FeatureGate>
        </>
    );
}
