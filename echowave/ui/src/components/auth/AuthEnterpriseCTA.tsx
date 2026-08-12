"use client";

// Enterprise call-to-action rendered inside the auth brand panel. Opens the
// SAME in-app Enterprise lead modal used post-login. Rebranded for Decibyl
// with the nAutomation Labs blue CTA styling.

import posthog from "posthog-js";
import { useState } from "react";

import { EnterpriseModal } from "@/components/lead-forms/EnterpriseModal";
import { Button } from "@/components/ui/button";
import { PostHogEvent } from "@/constants/posthog-events";

export function AuthEnterpriseCTA() {
  const [open, setOpen] = useState(false);

  const openModal = () => {
    setOpen(true);
    posthog.capture(PostHogEvent.ENTERPRISE_LEAD_OPENED, { source: "auth_page" });
  };

  return (
    <>
      <Button
        onClick={openModal}
        data-testid="auth-enterprise-cta"
        // The default variant already IS the accent. This carried a hand-written
        // bg + hover + a cyan glow shadow left over from the blue brand, which
        // put a blue halo under an orange button on the first screen a customer
        // sees. One accent, defined in one place.
        className="w-full"
      >
        Talk to Sales →
      </Button>
      <EnterpriseModal open={open} onOpenChange={setOpen} source="auth_page" />
    </>
  );
}
