"use client";

import { Suspense } from "react";

import { ApprovalsSection } from "@/components/ApprovalsSection";
import { SimpleModeSwitch } from "@/components/care/SimpleModeSwitch";
import { DecibylAppsSection } from "@/components/DecibylAppsSection";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { MfaSection } from "@/components/MfaSection";
import { OrganizationPreferencesSection } from "@/components/OrganizationPreferencesSection";
import { ThemeModeSection } from "@/components/ThemeModeSection";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { UnsavedChangesProvider } from "@/context/UnsavedChangesContext";
import { useSimpleMode } from "@/lib/care/simpleMode";
import { useFeature } from "@/lib/features";

/**
 * General: the workspace's defaults, appearance and sign-in security. With
 * the Settings shell on (``workspaceOnly``) it is "Workspace defaults" under
 * the workspace's heading: appearance moves to Account and two-step sign-in
 * to Privacy and security, so nothing personal sits among the team's
 * settings.
 */
export default function GeneralSettings({ workspaceOnly = false }: { workspaceOnly?: boolean }) {
  const approvals = useFeature("approvals");
  const decibylApps = useFeature("decibyl_channels");
  // Simple mode (stream `care`): a person's own preference, shown only
  // where it is offered. In the Settings shell it is on Account instead.
  const simpleMode = useSimpleMode();
  // Several cards on this page hold editable state — preferences, telemetry
  // credentials — and until this wrapper existed, clicking away from a
  // half-filled form discarded it without a word. The provider is the same one
  // the agent settings screen uses; the sections register themselves.
  return (
    <UnsavedChangesProvider>
      <PageHeader
        title={workspaceOnly ? "Workspace defaults" : "General"}
        description={
          workspaceOnly
            ? "The team's own defaults: changing them changes them for everyone in this workspace, never anybody's personal settings."
            : "Your workspace's defaults, how the app looks, and how you sign in. Tool credentials, MCP and tracing are under Advanced."
        }
      />
      {/* Two columns from lg up. As a single max-w-2xl column this page put a
          670px stack of cards in the middle of a 1190px content area and left
          the rest empty; the cards are short and independent, so they tile. */}
      <PageBody className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Preferences</CardTitle>
            <CardDescription>
              Set organization-wide defaults such as the test phone number and
              timezone.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <OrganizationPreferencesSection />
          </CardContent>
        </Card>

        {!workspaceOnly && (
        <Card>
          <CardHeader>
            <CardTitle>Appearance</CardTitle>
            <CardDescription>
              Light, dark, or whatever this device is set to -- and, if you
              want one, a theme. Saved on this device.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ThemeModeSection />
          </CardContent>
        </Card>
        )}

        {!workspaceOnly && simpleMode.offered && (
          <Card id="simple-mode">
            <CardHeader>
              <CardTitle>Simple mode</CardTitle>
              <CardDescription>
                Just for you, on every device you sign in on. Nobody else in
                the workspace is changed.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <SimpleModeSwitch />
            </CardContent>
          </Card>
        )}

        {!workspaceOnly && (
        <Card>
          <CardHeader>
            <CardTitle>Security</CardTitle>
            <CardDescription>
              Require a code from an authenticator app at sign-in, on top of
              your password.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <MfaSection />
          </CardContent>
        </Card>
        )}
        {decibylApps && (
          <Card id="decibyl-apps">
            <CardHeader>
              <CardTitle>Decibyl in your apps</CardTitle>
              <CardDescription>
                Ask Decibyl from WhatsApp, Telegram, Slack or Teams, as
                yourself. Actions that need your OK arrive as buttons there.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Suspense fallback={null}>
                <DecibylAppsSection />
              </Suspense>
            </CardContent>
          </Card>
        )}

        {approvals && (
          <Card>
            <CardHeader>
              <CardTitle>Approvals</CardTitle>
              <CardDescription>
                Who must approve what: a card, an agent&apos;s question, or a
                document going out, by kind and amount. And the record of who
                did.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ApprovalsSection />
            </CardContent>
          </Card>
        )}

      </PageBody>
    </UnsavedChangesProvider>
  );
}
