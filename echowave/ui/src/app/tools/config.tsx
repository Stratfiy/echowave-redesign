"use client";

import { Calculator, Cog, Globe, type LucideIcon, PhoneForwarded, PhoneOff, Puzzle } from "lucide-react";
import { type ReactNode } from "react";

import type {
    CalculatorToolDefinition,
    EndCallConfig,
    EndCallToolDefinition,
    HttpApiToolDefinition,
    McpToolDefinition,
    PresetToolParameter,
    ToolParameter,
    TransferCallConfig,
    TransferCallToolDefinition,
} from "@/client/types.gen";

export type ToolCategory = "http_api" | "end_call" | "transfer_call" | "calculator" | "native" | "integration" | "mcp";

export type EndCallMessageType = "none" | "custom" | "audio";
export type TransferDestinationSource = "static" | "dynamic";

export interface TransferResolverConfig {
    type: "http";
    url: string;
    headers?: Record<string, string> | null;
    credential_uuid?: string | null;
    timeout_ms: number;
    wait_message?: string | null;
    parameters?: ToolParameter[] | null;
    preset_parameters?: PresetToolParameter[] | null;
}

export interface ExtendedTransferCallConfig extends TransferCallConfig {
    destination_source?: TransferDestinationSource;
    resolver?: TransferResolverConfig | null;
}

export interface ToolCategoryConfig {
    value: ToolCategory;
    label: string;
    description: string;
    icon: LucideIcon;
    iconName: string; // String name for storing in database
    iconColor: string;
    disabled?: boolean;
    autoFill?: {
        name: string;
        description: string;
    };
}

export const TOOL_CATEGORIES: ToolCategoryConfig[] = [
    {
        value: "http_api",
        label: "External HTTP API",
        description: "Make HTTP requests to external APIs",
        icon: Globe,
        iconName: "globe",
        iconColor: "#3B82F6",
    },
    {
        value: "end_call",
        label: "End Call",
        description: "End the call when conditions are met",
        icon: PhoneOff,
        iconName: "phone-off",
        iconColor: "#EF4444",
        autoFill: {
            name: "End Call",
            description: "End the call when either user asks to disconnect the call, or when you believe its time to end the conversation",
        },
    },
    {
        value: "transfer_call",
        label: "Transfer Call",
        description: "Transfer the call to another phone number (Twilio only)",
        icon: PhoneForwarded,
        iconName: "phone-forwarded",
        iconColor: "#10B981",
        autoFill: {
            name: "Transfer Call",
            description: "Transfer the caller to another phone number when requested",
        },
    },
    {
        value: "calculator",
        label: "Calculator",
        description: "Built-in calculator for arithmetic operations",
        icon: Calculator,
        iconName: "calculator",
        iconColor: "#F59E0B",
        autoFill: {
            name: "Calculator",
            description: "Perform arithmetic calculations (supports +, -, *, /, **, %, and parentheses)",
        },
    },
    {
        value: "mcp",
        label: "MCP Server",
        description: "Connect a customer MCP server; its tools become available to the agent",
        icon: Puzzle,
        iconName: "puzzle",
        iconColor: "#8B5CF6",
    },
    {
        value: "native",
        label: "Native",
        description: "Built-in tools like DTMF keypad tones and SMS",
        icon: Cog,
        iconName: "cog",
        iconColor: "#6B7280",
    },
    {
        value: "integration",
        label: "Integration",
        description: "Third-party integrations like Google Calendar and Gmail",
        icon: Puzzle,
        iconName: "puzzle",
        iconColor: "#8B5CF6",
    },
];

export type NativeToolType = "dtmf" | "sms";

export interface NativeToolConfig {
    native_type: NativeToolType;
    parameters?: ToolParameter[] | null;
}

export interface NativeToolDefinition {
    schema_version: 1;
    type: "native";
    config: NativeToolConfig;
}

export const NATIVE_TOOL_TYPES: {
    value: NativeToolType;
    label: string;
    description: string;
    defaultParameters: ToolParameter[];
    autoFill: { name: string; description: string };
}[] = [
    {
        value: "dtmf",
        label: "Send DTMF Tones",
        description: "Send keypad tones during the call — e.g. to navigate an IVR menu",
        defaultParameters: [
            {
                name: "digits",
                type: "string",
                description: "The digits to send, e.g. '1', '4#', '9302'.",
                required: true,
            },
        ],
        autoFill: {
            name: "Send DTMF",
            description: "Send keypad tones (0-9, *, #) during the call when needed, e.g. to navigate an automated phone menu.",
        },
    },
    {
        value: "sms",
        label: "Send SMS",
        description: "Send a text message during or after the call (Twilio only)",
        defaultParameters: [
            {
                name: "to_number",
                type: "string",
                description: "The phone number to send the SMS to, in E.164 format (e.g. +14155550100).",
                required: true,
            },
            {
                name: "message",
                type: "string",
                description: "The SMS message body to send.",
                required: true,
            },
        ],
        autoFill: {
            name: "Send SMS",
            description: "Send a text message to the caller when requested, e.g. to share a link or confirmation.",
        },
    },
];

export type IntegrationProvider = "google";
export type IntegrationAction = "google_calendar_create_event" | "google_gmail_send";

export interface IntegrationToolConfig {
    provider: IntegrationProvider;
    action: IntegrationAction;
    connection_id: number;
    parameters?: ToolParameter[] | null;
}

export interface IntegrationToolDefinition {
    schema_version: 1;
    type: "integration";
    config: IntegrationToolConfig;
}

export const GOOGLE_INTEGRATION_ACTIONS: {
    value: IntegrationAction;
    label: string;
    description: string;
    defaultParameters: ToolParameter[];
    autoFill: { name: string; description: string };
}[] = [
    {
        value: "google_calendar_create_event",
        label: "Create Calendar Event",
        description: "Create an event on the connected Google Calendar",
        defaultParameters: [
            { name: "summary", type: "string", description: "Event title.", required: true },
            { name: "start_time", type: "string", description: "Event start time, RFC3339 (e.g. 2026-08-01T15:00:00-07:00).", required: true },
            { name: "end_time", type: "string", description: "Event end time, RFC3339.", required: true },
            { name: "description", type: "string", description: "Event description.", required: false },
            { name: "attendee_emails", type: "string", description: "Comma-separated attendee email addresses.", required: false },
        ],
        autoFill: {
            name: "Create Calendar Event",
            description: "Create a calendar event when the caller wants to schedule something.",
        },
    },
    {
        value: "google_gmail_send",
        label: "Send Email",
        description: "Send an email from the connected Google account",
        defaultParameters: [
            { name: "to", type: "string", description: "Recipient email address.", required: true },
            { name: "subject", type: "string", description: "Email subject.", required: true },
            { name: "body", type: "string", description: "Email body text.", required: true },
        ],
        autoFill: {
            name: "Send Email",
            description: "Send an email when the caller requests information be emailed to them.",
        },
    },
];

export function getCategoryConfig(category: ToolCategory): ToolCategoryConfig | undefined {
    return TOOL_CATEGORIES.find(c => c.value === category);
}

export function getToolIcon(category: string): LucideIcon {
    const config = TOOL_CATEGORIES.find(c => c.value === category);
    return config?.icon ?? Globe;
}

export function getToolIconColor(category: string, fallbackColor?: string): string {
    const config = TOOL_CATEGORIES.find(c => c.value === category);
    return config?.iconColor ?? fallbackColor ?? "#3B82F6";
}

export function renderToolIcon(category: string, className: string = "w-5 h-5 text-white"): ReactNode {
    const Icon = getToolIcon(category);
    return <Icon className={className} />;
}

export function getToolTypeLabel(category: string): string {
    switch (category) {
        case "end_call":
            return "End Call Tool";
        case "transfer_call":
            return "Transfer Call Tool";
        case "http_api":
            return "HTTP API Tool";
        case "calculator":
            return "Calculator Tool";
        case "native":
            return "Native Tool";
        case "integration":
            return "Integration Tool";
        case "mcp":
            return "MCP Server Tool";
        default:
            return "Tool";
    }
}

export const DEFAULT_END_CALL_REASON_DESCRIPTION =
    "The reason for ending the call (e.g., 'voicemail_detected', 'issue_resolved', 'customer_requested')";

export const DEFAULT_END_CALL_CONFIG: EndCallConfig = {
    messageType: "none",
    customMessage: "",
    endCallReason: false,
};

export const DEFAULT_TRANSFER_CALL_CONFIG: TransferCallConfig = {
    destination: "",
    messageType: "none",
    customMessage: "",
    timeout: 30,
};

export type ToolDefinition =
    | HttpApiToolDefinition
    | EndCallToolDefinition
    | TransferCallToolDefinition
    | CalculatorToolDefinition
    | McpToolDefinition
    | NativeToolDefinition
    | IntegrationToolDefinition;

export function createEndCallDefinition(config: EndCallConfig): EndCallToolDefinition {
    return {
        schema_version: 1,
        type: "end_call",
        config,
    };
}

export function createTransferCallDefinition(config: TransferCallConfig): TransferCallToolDefinition {
    return {
        schema_version: 1,
        type: "transfer_call",
        config,
    };
}

export function createHttpApiDefinition(): HttpApiToolDefinition {
    return {
        schema_version: 1,
        type: "http_api",
        config: {
            method: "POST",
            url: "",
        },
    };
}

export function createCalculatorDefinition(): CalculatorToolDefinition {
    return {
        schema_version: 1,
        type: "calculator",
    };
}

export const MCP_URL_PATTERN = /^https?:\/\//i;

export function createMcpDefinition(
    url: string,
    credentialUuid: string,
    toolsFilterCsv: string,
): McpToolDefinition {
    return {
        schema_version: 1,
        type: "mcp" as const,
        config: {
            transport: "streamable_http" as const,
            url: url.trim(),
            credential_uuid: credentialUuid || null,
            tools_filter: toolsFilterCsv
                .split(",")
                .map((s) => s.trim())
                .filter((s) => s.length > 0),
        },
    };
}

export function createNativeDefinition(
    nativeType: NativeToolType,
    parameters: ToolParameter[],
): NativeToolDefinition {
    return {
        schema_version: 1,
        type: "native",
        config: {
            native_type: nativeType,
            parameters,
        },
    };
}

export function createIntegrationDefinition(
    provider: IntegrationProvider,
    action: IntegrationAction,
    connectionId: number,
    parameters: ToolParameter[],
): IntegrationToolDefinition {
    return {
        schema_version: 1,
        type: "integration",
        config: {
            provider,
            action,
            connection_id: connectionId,
            parameters,
        },
    };
}

export function createToolDefinition(category: ToolCategory): ToolDefinition {
    switch (category) {
        case "end_call":
            return createEndCallDefinition(DEFAULT_END_CALL_CONFIG);
        case "transfer_call":
            return createTransferCallDefinition(DEFAULT_TRANSFER_CALL_CONFIG);
        case "calculator":
            return createCalculatorDefinition();
        case "http_api":
        default:
            return createHttpApiDefinition();
    }
}
