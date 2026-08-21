"use client";

import { ExternalLink } from "lucide-react";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { MCPSection } from "@/components/MCPSection";
import { MfaSection } from "@/components/MfaSection";
import { OrganizationMembersSection } from "@/components/OrganizationMembersSection";
import { OrganizationPreferencesSection } from "@/components/OrganizationPreferencesSection";
import { TelemetrySection } from "@/components/TelemetrySection";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function SettingsPage() {
  return (
    <>
      <PageHeader
        title="Platform Settings"
        description="Manage your platform configuration and integrations."
      />
      {/* Two columns from lg up. As a single max-w-2xl column this page put a
          670px stack of cards in the middle of a 1190px content area and left
          the rest empty; the cards are short and independent, so they tile. */}
      <PageBody className="grid items-start gap-6 lg:grid-cols-2">
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

        <Card>
          <CardHeader>
            <CardTitle>Team</CardTitle>
            <CardDescription>
              Who has access to this organization, and what they can do.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <OrganizationMembersSection />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>MCP Server</CardTitle>
            <CardDescription>
              Let AI agents access your Decibyl workspace and documentation via
              the Model Context Protocol.{" "}
              <a
                href="https://docs.decibyl.ai/integrations/mcp"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 underline"
              >
                Learn more <ExternalLink className="h-3 w-3" />
              </a>
            </CardDescription>
          </CardHeader>
          <CardContent>
            <MCPSection />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Telemetry</CardTitle>
            <CardDescription>
              Configure Langfuse tracing for your voice agent calls.{" "}
              <a
                href="https://docs.decibyl.ai/configurations/tracing"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 underline"
              >
                Learn more <ExternalLink className="h-3 w-3" />
              </a>
            </CardDescription>
          </CardHeader>
          <CardContent>
            <TelemetrySection />
          </CardContent>
        </Card>

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
      </PageBody>
    </>
  );
}
