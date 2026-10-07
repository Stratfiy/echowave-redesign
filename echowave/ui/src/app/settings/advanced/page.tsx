"use client";

import { ExternalLink } from "lucide-react";

import { CredentialsSection } from "@/components/CredentialsSection";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { MCPSection } from "@/components/MCPSection";
import { TelemetrySection } from "@/components/TelemetrySection";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { UnsavedChangesProvider } from "@/context/UnsavedChangesContext";

/**
 * Settings -> Advanced: what a developer or an admin sets up, kept out of
 * General so everyday settings are not mixed with secrets and tracing.
 * Writes are still gated by role on the server; this page only groups them.
 */
export default function AdvancedSettingsPage() {
  return (
    <UnsavedChangesProvider>
      <PageHeader title="Advanced" description="Tool credentials, MCP access and call tracing." />
      <PageBody className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Tool credentials</CardTitle>
            <CardDescription>
              The secrets your tools authenticate with. Rotate one and every tool
              using it picks the new value up on its next call.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <CredentialsSection />
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
              Configure Langfuse tracing for calls your agents take.{" "}
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

      </PageBody>
    </UnsavedChangesProvider>
  );
}
