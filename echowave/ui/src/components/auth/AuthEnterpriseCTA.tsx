"use client";

// Enterprise call-to-action rendered inside the auth brand panel. Opens the
// SAME in-app Enterprise lead modal used post-login. Rebranded for EchoWave
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
        className="w-full bg-brand-blue text-white shadow-[0_10px_30px_-12px_rgba(40,179,240,0.6)] hover:bg-brand-blue-hover"
      >
        Talk to Sales →
      </Button>
      <EnterpriseModal open={open} onOpenChange={setOpen} source="auth_page" />
    </>
  );
}
