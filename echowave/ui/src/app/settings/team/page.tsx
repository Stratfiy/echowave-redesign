"use client";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { OrganizationMembersSection } from "@/components/OrganizationMembersSection";

/** Who has access to this workspace, and what they can do. Was a card on
 *  /settings (#team); a section of its own now. */
export default function TeamSettingsPage() {
  return (
    <>
      <PageHeader title="Team" description="Who has access to this workspace, and what they can do." />
      <PageBody className="max-w-3xl">
        <OrganizationMembersSection />
      </PageBody>
    </>
  );
}
