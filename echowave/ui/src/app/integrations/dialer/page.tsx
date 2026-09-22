"use client";

import { DialerScreen } from "@/components/integrations/DialerScreen";
import { useIntegrationsTabs } from "@/components/integrations/integrationsTabs";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

export default function DialerPage() {
    const tabs = useIntegrationsTabs();
    return (
        <>
            <PageHeader
                tabs={tabs}
                title="Your dialer"
                description="Connect Exotel or Tata Smartflo so the call coach can hear your team's calls."
            />
            <PageBody className="mx-auto max-w-3xl">
                <DialerScreen />
            </PageBody>
        </>
    );
}
