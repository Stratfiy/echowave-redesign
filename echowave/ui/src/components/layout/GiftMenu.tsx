"use client";

/**
 * The free credits and the referral link, behind a gift beside the credits
 * chip.
 *
 * Both lived as cards on the home screen, under the composer. They are
 * about the account, not the conversation: on the screen whose job is a
 * chat with Decibyl they pushed the text box up and sat beneath it as two
 * cards of chores. Here they are one tap from the number they add to.
 */

import { Gift } from "lucide-react";

import { InviteCard } from "@/components/home/InviteCard";
import { OnboardingChecklist } from "@/components/home/OnboardingChecklist";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export function GiftMenu() {
  return (
    <Popover>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Free credits and referrals"
              data-testid="gift-menu-trigger"
            >
              <Gift className="h-5 w-5" />
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>Free credits and referrals</TooltipContent>
      </Tooltip>
      <PopoverContent
        align="end"
        className="max-h-[80vh] w-[min(28rem,calc(100vw-2rem))] space-y-4 overflow-y-auto p-4"
      >
        <OnboardingChecklist />
        <InviteCard compact />
      </PopoverContent>
    </Popover>
  );
}
