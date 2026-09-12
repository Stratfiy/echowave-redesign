"use client";

import { format } from "date-fns";
import { CalendarIcon, CheckCircle2, ChevronRight, Clipboard, Download, ExternalLink, FileDown, Fingerprint, FlaskConical, Mic, PhoneOff, Plus, Rocket, Settings, Share2, Tags, Trash2, Trash2Icon, Variable } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
    downloadWorkflowReportApiV1WorkflowWorkflowIdReportGet,
    getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet,
    getWorkflowApiV1WorkflowFetchWorkflowIdGet,
    listToolsApiV1ToolsGet,
} from "@/client/sdk.gen";
import type { OutcomeAction } from "@/client/types.gen";
import type {
    WorkflowResponse,
} from "@/client/types.gen";
import { ShareAgentDialog } from "@/components/agent/ShareAgentDialog";
import {
    type ModelConfigurationDefaultsV2,
} from "@/components/AIModelConfigurationV2Editor";
import { FallbackChain } from "@/components/FallbackChain";
import { FlowEdge, FlowNode } from "@/components/flow/types";
import { LLMConfigSelector } from "@/components/LLMConfigSelector";
import SpinLoader from "@/components/SpinLoader";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
    type CallOutcome,
    DEFAULT_CALL_OUTCOMES,
    MAX_CALL_OUTCOMES,
    normaliseOutcomeCode,
    outcomesToSave,
    UNCLEAR_OUTCOME,
} from "@/constants/callOutcomes";
import { SETTINGS_DOCUMENTATION_URLS } from "@/constants/documentation";
import { UnsavedChangesProvider, useUnsavedChanges, useUnsavedChangesContext } from "@/context/UnsavedChangesContext";
import { useAuth } from "@/lib/auth";
import logger from "@/lib/logger";
import { cn } from "@/lib/utils";
import {
    DEFAULT_VOICEMAIL_DETECTION_CONFIGURATION,
    type FallbackService,
    type RecordingConfiguration,
    resolveWorkflowConfigurations,
    type VoicemailDetectionConfiguration,
    type WorkflowConfigurations,
} from "@/types/workflow-configurations";

import { AgentHeader } from "../components/AgentHeader";
import { AgentTabs } from "../components/AgentTabs";
import { QaCard } from "../components/QaCard";
import { useWorkflowState } from "../hooks/useWorkflowState";
import { ALWAYS_AVAILABLE, type ToolParameter,toolParameters } from "./outcomeArguments";
import { DEFAULT_TAB, isTabId, type TabId, TABS } from "./tabs";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_VOICEMAIL_SYSTEM_PROMPT = `You are a voicemail detection classifier for an OUTBOUND calling system. A bot has called a phone number and you need to determine if a human answered or if the call went to voicemail based on the provided text.

HUMAN ANSWERED - LIVE CONVERSATION (respond "CONVERSATION"):
- Personal greetings: "Hello?", "Hi", "Yeah?", "John speaking"
- Interactive responses: "Who is this?", "What do you want?", "Can I help you?"
- Conversational tone expecting back-and-forth dialogue
- Questions directed at the caller: "Hello? Anyone there?"
- Informal responses: "Yep", "What's up?", "Speaking"
- Natural, spontaneous speech patterns
- Immediate acknowledgment of the call

VOICEMAIL SYSTEM (respond "VOICEMAIL"):
- Automated voicemail greetings: "Hi, you've reached [name], please leave a message"
- Phone carrier messages: "The number you have dialed is not in service", "Please leave a message", "All circuits are busy"
- Professional voicemail: "This is [name], I'm not available right now"
- Instructions about leaving messages: "leave a message", "leave your name and number"
- References to callback or messaging: "call me back", "I'll get back to you"
- Carrier system messages: "mailbox is full", "has not been set up"
- Business hours messages: "our office is currently closed"

Respond with ONLY "CONVERSATION" if a person answered, or "VOICEMAIL" if it's voicemail/recording.`;

// Sidebar navigation items


// ---------------------------------------------------------------------------
// Section: Report
// ---------------------------------------------------------------------------

function ReportSection({ workflowId }: { workflowId: number }) {
    const [startDate, setStartDate] = useState<Date | undefined>(undefined);
    const [startTime, setStartTime] = useState("00:00");
    const [endDate, setEndDate] = useState<Date | undefined>(undefined);
    const [endTime, setEndTime] = useState("23:59");
    const [isPopoverOpen, setIsPopoverOpen] = useState(false);
    const [isDownloading, setIsDownloading] = useState(false);

    const buildDateTime = (date: Date | undefined, time: string): string | undefined => {
        if (!date) return undefined;
        const [hours, minutes] = time.split(":").map(Number);
        const combined = new Date(date);
        combined.setHours(hours, minutes, 0, 0);
        return combined.toISOString();
    };

    const handleDownload = async () => {
        setIsDownloading(true);
        setIsPopoverOpen(false);
        try {
            const response = await downloadWorkflowReportApiV1WorkflowWorkflowIdReportGet({
                path: { workflow_id: workflowId },
                query: {
                    start_date: buildDateTime(startDate, startTime),
                    end_date: buildDateTime(endDate, endTime),
                },
                parseAs: "blob",
            });

            if (response.data) {
                const blob = response.data as Blob;
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `workflow_${workflowId}_report.csv`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);
            } else {
                toast.error("Failed to download report");
            }
        } catch (err) {
            logger.error(`Failed to download workflow report: ${err}`);
            toast.error("Failed to download report");
        } finally {
            setIsDownloading(false);
        }
    };

    const handleClear = () => {
        setStartDate(undefined);
        setStartTime("00:00");
        setEndDate(undefined);
        setEndTime("23:59");
    };

    return (
        <Card id="report">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <FileDown className="h-4 w-4" />
                    Report
                </CardTitle>
                <CardDescription>
                    Download a CSV report of completed runs for this agent, optionally filtered by date range.
                </CardDescription>
            </CardHeader>
            <CardFooter className="border-t pt-6">
                <Popover open={isPopoverOpen} onOpenChange={setIsPopoverOpen}>
                    <PopoverTrigger asChild>
                        <Button variant="outline" disabled={isDownloading}>
                            <Download className="h-4 w-4 mr-2" />
                            Download Report
                        </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-auto p-4" align="start">
                        <div className="space-y-4">
                            <div className="text-sm font-medium">Filter by date range</div>
                            <div className="grid gap-3">
                                <div className="space-y-1.5">
                                    <Label className="text-xs">From</Label>
                                    <div className="flex gap-2">
                                        <Popover>
                                            <PopoverTrigger asChild>
                                                <Button variant="outline" size="sm" className="w-[140px] justify-start text-left font-normal">
                                                    <CalendarIcon className="mr-2 h-3.5 w-3.5" />
                                                    {startDate ? format(startDate, "MMM dd, yyyy") : "Start date"}
                                                </Button>
                                            </PopoverTrigger>
                                            <PopoverContent className="w-auto p-0" align="start">
                                                <Calendar
                                                    mode="single"
                                                    selected={startDate}
                                                    onSelect={setStartDate}
                                                    disabled={(date) => (endDate ? date > endDate : false)}
                                                />
                                            </PopoverContent>
                                        </Popover>
                                        <Input
                                            type="time"
                                            value={startTime}
                                            onChange={(e) => setStartTime(e.target.value)}
                                            className="w-[100px] h-8 text-xs"
                                        />
                                    </div>
                                </div>
                                <div className="space-y-1.5">
                                    <Label className="text-xs">To</Label>
                                    <div className="flex gap-2">
                                        <Popover>
                                            <PopoverTrigger asChild>
                                                <Button variant="outline" size="sm" className="w-[140px] justify-start text-left font-normal">
                                                    <CalendarIcon className="mr-2 h-3.5 w-3.5" />
                                                    {endDate ? format(endDate, "MMM dd, yyyy") : "End date"}
                                                </Button>
                                            </PopoverTrigger>
                                            <PopoverContent className="w-auto p-0" align="start">
                                                <Calendar
                                                    mode="single"
                                                    selected={endDate}
                                                    onSelect={setEndDate}
                                                    disabled={(date) => (startDate ? date < startDate : false)}
                                                />
                                            </PopoverContent>
                                        </Popover>
                                        <Input
                                            type="time"
                                            value={endTime}
                                            onChange={(e) => setEndTime(e.target.value)}
                                            className="w-[100px] h-8 text-xs"
                                        />
                                    </div>
                                </div>
                            </div>
                            <Separator />
                            <div className="flex justify-between">
                                <Button variant="ghost" size="sm" onClick={handleClear}>
                                    Clear
                                </Button>
                                <Button size="sm" onClick={handleDownload} disabled={isDownloading}>
                                    <Download className="h-3.5 w-3.5 mr-1.5" />
                                    {startDate || endDate ? "Download Filtered" : "Download All"}
                                </Button>
                            </div>
                        </div>
                    </PopoverContent>
                </Popover>
            </CardFooter>
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Section: General
// ---------------------------------------------------------------------------

/** One phrase per line in a text box; trimmed, blanks dropped. */
function linesToList(text: string): string[] {
    return text
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
}

function GeneralSection({
    workflowConfigurations,
    workflowName,
    onSave,
    modelConfigurationDefaults,
}: {
    workflowConfigurations: WorkflowConfigurations;
    workflowName: string;
    onSave: (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;
    modelConfigurationDefaults: ModelConfigurationDefaultsV2 | null;
}) {
    const [name, setName] = useState(workflowName);
    const [endCallPhrasesText, setEndCallPhrasesText] = useState(
        (workflowConfigurations.end_call_phrases ?? []).join("\n"),
    );
    const [endCallFarewell, setEndCallFarewell] = useState(
        workflowConfigurations.end_call_farewell ?? "",
    );
    const [recordingConfig, setRecordingConfig] = useState<RecordingConfiguration>(
        workflowConfigurations.recording_configuration ?? { enabled: true },
    );
    const [maxCallDuration, setMaxCallDuration] = useState(workflowConfigurations.max_call_duration);
    const [maxUserIdleTimeout, setMaxUserIdleTimeout] = useState(workflowConfigurations.max_user_idle_timeout);
    const [includeTranscriptEndTimestamps, setIncludeTranscriptEndTimestamps] = useState(
        workflowConfigurations.transcript_configuration?.include_end_timestamps ?? false,
    );
    const [fallbackTts, setFallbackTts] = useState<FallbackService[]>(
        workflowConfigurations.fallback_tts ?? [],
    );
    const [fallbackStt, setFallbackStt] = useState<FallbackService[]>(
        workflowConfigurations.fallback_stt ?? [],
    );
    // Advanced starts open only when it already holds something non-default,
    // so nothing a person set is hidden behind a closed group.
    const advancedTouched = includeTranscriptEndTimestamps;

    const [isSaving, setIsSaving] = useState(false);

    const isDirty = useMemo(() => {
        return (
            name !== workflowName ||
            recordingConfig.enabled !==
            (workflowConfigurations.recording_configuration?.enabled ?? true) ||
            JSON.stringify(linesToList(endCallPhrasesText)) !==
            JSON.stringify(workflowConfigurations.end_call_phrases ?? []) ||
            endCallFarewell !== (workflowConfigurations.end_call_farewell ?? "") ||
            maxCallDuration !== workflowConfigurations.max_call_duration ||
            maxUserIdleTimeout !== workflowConfigurations.max_user_idle_timeout ||
            JSON.stringify(fallbackTts) !== JSON.stringify(workflowConfigurations.fallback_tts ?? []) ||
            JSON.stringify(fallbackStt) !== JSON.stringify(workflowConfigurations.fallback_stt ?? []) ||
            includeTranscriptEndTimestamps !==
            (workflowConfigurations.transcript_configuration?.include_end_timestamps ?? false)
        );
    }, [name, workflowName, recordingConfig, endCallPhrasesText, endCallFarewell, maxCallDuration, maxUserIdleTimeout, fallbackTts, fallbackStt, includeTranscriptEndTimestamps, workflowConfigurations]);

    useUnsavedChanges("general", isDirty);

    const handleSave = async () => {
        setIsSaving(true);
        try {
            // Spread first: the turn-taking, fillers, noise and keypad
            // settings live behind the model tiles now and are saved from
            // there, so this card must carry them through untouched.
            await onSave(
                {
                    ...workflowConfigurations,
                    recording_configuration: recordingConfig,
                    end_call_phrases: linesToList(endCallPhrasesText),
                    end_call_farewell: endCallFarewell.trim() || null,
                    max_call_duration: maxCallDuration,
                    max_user_idle_timeout: maxUserIdleTimeout,
                    fallback_tts: fallbackTts,
                    fallback_stt: fallbackStt,
                    transcript_configuration: {
                        ...(workflowConfigurations.transcript_configuration ?? {}),
                        include_end_timestamps: includeTranscriptEndTimestamps,
                    },
                },
                name,
            );
        } catch (error) {
            console.error("Failed to save general settings:", error);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Card id="general">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <Settings className="h-4 w-4" />
                    Calls
                </CardTitle>
                <CardDescription>The agent&apos;s name, what happens when a provider fails, and when a call ends. How it listens, thinks and speaks is behind the pencil on each tile of the Assistant tab.{" "}
                    <a href={SETTINGS_DOCUMENTATION_URLS.general} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">Learn more <ExternalLink className="h-3 w-3" /></a>
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                {/* Agent Name */}
                <div className="space-y-2">
                    <Label htmlFor="workflow_name" className="text-sm font-medium">Agent Name</Label>
                    <Input
                        id="workflow_name"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="Enter Agent name"
                    />
                </div>

                <Separator />
                <SettingsGroup
                    title="Reliability and limits"
                    blurb="What happens when a provider fails, and when a call should end."
                    defaultOpen={true}
                >
                {/* Fallbacks */}
                <div className="space-y-5">
                    <div>
                        <h3 className="text-sm font-medium">Fallbacks</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Where the call goes if a provider fails while someone is on the
                            line. Tried in order, and only for a provider reporting a problem
                            it expects to survive — a provider saying the call cannot
                            continue still ends it.
                        </p>
                    </div>

                    <FallbackChain
                        label="Voice"
                        kind="tts"
                        description="A voice that stops mid-sentence is dead air, which is the worst thing a caller can be handed."
                        schemas={modelConfigurationDefaults?.byok?.pipeline?.tts}
                        value={fallbackTts}
                        onChange={setFallbackTts}
                    />

                    <FallbackChain
                        label="Transcriber"
                        kind="stt"
                        description="A transcriber that fails leaves the agent unable to hear, so it waits through a caller who is already talking."
                        schemas={modelConfigurationDefaults?.byok?.pipeline?.stt}
                        value={fallbackStt}
                        onChange={setFallbackStt}
                    />
                </div>

                        <Separator />
                {/* Call Management */}
                <div className="space-y-4">
                    <div>
                        <h3 className="text-sm font-medium">Call Management</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Configure call duration limits and idle timeout settings.
                        </p>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <Label htmlFor="max_call_duration" className="text-xs">Max Call Duration (seconds)</Label>
                            <Input
                                id="max_call_duration"
                                type="number"
                                min="1"
                                value={maxCallDuration}
                                onChange={(e) => {
                                    const value = parseInt(e.target.value);
                                    if (!isNaN(value) && value > 0) setMaxCallDuration(value);
                                }}
                            />
                            <p className="text-xs text-muted-foreground">Default: 600 (10 minutes)</p>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="max_user_idle_timeout" className="text-xs">
                                Max User Idle Timeout (seconds)
                            </Label>
                            <Input
                                id="max_user_idle_timeout"
                                type="number"
                                min="1"
                                value={maxUserIdleTimeout}
                                onChange={(e) => {
                                    const value = parseInt(e.target.value);
                                    if (!isNaN(value) && value > 0) setMaxUserIdleTimeout(value);
                                }}
                            />
                            <p className="text-xs text-muted-foreground">Default: 10 seconds</p>
                        </div>
                    </div>
                </div>

                <Separator />

                {/* Hang up on goodbye. With the other ways a call ends. */}
                <div className="space-y-4">
                    <div>
                        <h3 className="text-sm font-medium">Hang up on goodbye</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            When the caller says one of these, the agent says its farewell
                            and ends the call at once, with no reply in between. A short
                            utterance that contains a phrase counts; a long sentence has
                            to be the phrase. Empty means off.
                        </p>
                    </div>
                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="end-call-phrases" className="text-xs">
                                Goodbye phrases, one per line
                            </Label>
                            <Textarea
                                id="end-call-phrases"
                                rows={4}
                                value={endCallPhrasesText}
                                onChange={(e) => setEndCallPhrasesText(e.target.value)}
                                placeholder={"bye\nthat's all\nthank you bye\nசரி வைக்கிறேன்"}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="end-call-farewell" className="text-xs">
                                Farewell the agent says first
                            </Label>
                            <Textarea
                                id="end-call-farewell"
                                rows={4}
                                value={endCallFarewell}
                                onChange={(e) => setEndCallFarewell(e.target.value)}
                                placeholder="Thank you for calling. Goodbye!"
                            />
                            <p className="text-xs text-muted-foreground">
                                Leave empty to hang up without a word.
                            </p>
                        </div>
                    </div>
                </div>
                </SettingsGroup>
                <Separator />
                <SettingsGroup
                    title="Audio"
                    blurb="Whether the call's audio is kept."
                    defaultOpen={true}
                >
                {/* Call recording. First in the group because it decides
                    whether there is any audio to talk about at all. */}
                <div className="space-y-4">
                    <div>
                        <h3 className="text-sm font-medium">Call recording</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Keep the audio of every call for review and QA. Switch
                            it off for a line where no voice data may be stored:
                            the transcript, outcome and usage are still kept, and
                            the agent stops telling callers the call is recorded.
                        </p>
                    </div>
                    <div className="flex items-center justify-between">
                        <Label htmlFor="recording-enabled" className="text-sm">
                            Record calls
                        </Label>
                        <Switch
                            id="recording-enabled"
                            checked={recordingConfig.enabled}
                            onCheckedChange={(checked) =>
                                setRecordingConfig({ ...recordingConfig, enabled: checked })
                            }
                        />
                    </div>
                    <p className="text-xs text-muted-foreground">
                        {recordingConfig.enabled
                            ? "On. Recordings follow the retention period set on the Privacy page."
                            : "Off. No audio is written for this agent's calls, so call review has the transcript only."}
                    </p>
                </div>

                </SettingsGroup>
                <Separator />
                <SettingsGroup
                    title="Advanced"
                    blurb="Transcript detail."
                    defaultOpen={advancedTouched}
                >
                {/* Transcript */}
                <div className="space-y-4">
                    <div>
                        <h3 className="text-sm font-medium">Transcript</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Include start and stop timestamps for each speaker in the uploaded transcript.
                        </p>
                    </div>
                    <div className="flex items-center justify-between">
                        <Label htmlFor="transcript-end-timestamps-enabled" className="text-sm">
                            Enhanced Timestamped Transcript
                        </Label>
                        <Switch
                            id="transcript-end-timestamps-enabled"
                            checked={includeTranscriptEndTimestamps}
                            onCheckedChange={setIncludeTranscriptEndTimestamps}
                        />
                    </div>
                    <div className="rounded-md border bg-muted/20 p-3">
                        <pre className="whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
                            {`[2026-07-06T10:00:00.000Z -> 2026-07-06T10:00:04.800Z] assistant: Can you confirm your date of birth?
[2026-07-06T10:00:06.200Z -> 2026-07-06T10:00:08.700Z] user: January fifth, nineteen ninety.`}
                        </pre>
                    </div>
                </div>

                </SettingsGroup>
            </CardContent>
            <CardFooter className="justify-end gap-3 border-t pt-6">
                {isDirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
                <Button onClick={handleSave} disabled={isSaving || !isDirty}>
                    {isSaving ? "Saving..." : "Save Call Settings"}
                </Button>
            </CardFooter>
        </Card>
    );
}

/**
 * One collapsible group of related settings.
 *
 * The General card carried nine sections, all expanded, so finding one meant
 * scrolling past the other eight -- and Fallbacks, which is the difference
 * between a failed provider and dead air on a call, sat eighth.
 *
 * ``defaultOpen`` is a starting state rather than a fixed one, and a group
 * holding a value somebody has already changed passes true: a setting that is
 * not on its default must never be hidden behind a click nobody knew to make.
 * ServiceConfigurationForm's advanced block reaches the same conclusion.
 */
function SettingsGroup({
    title,
    blurb,
    defaultOpen,
    children,
}: {
    title: string;
    blurb: string;
    defaultOpen: boolean;
    children: React.ReactNode;
}) {
    const [open, setOpen] = useState(defaultOpen);

    return (
        <Collapsible open={open} onOpenChange={setOpen}>
            <CollapsibleTrigger className="flex w-full items-start gap-2 text-left">
                <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground transition-transform data-[state=open]:rotate-90" />
                <span className="min-w-0">
                    <span className="block text-sm font-medium">{title}</span>
                    <span className="mt-0.5 block text-xs text-muted-foreground">{blurb}</span>
                </span>
            </CollapsibleTrigger>
            <CollapsibleContent className="space-y-6 pt-6">{children}</CollapsibleContent>
        </Collapsible>
    );
}

// ---------------------------------------------------------------------------
// Section: Template Variables
// ---------------------------------------------------------------------------

function TemplateVariablesSection({
    templateContextVariables,
    onSave,
}: {
    templateContextVariables: Record<string, string>;
    onSave: (variables: Record<string, string>) => Promise<void>;
}) {
    const [contextVars, setContextVars] = useState<Record<string, string>>(templateContextVariables);
    const [newKey, setNewKey] = useState("");
    const [newValue, setNewValue] = useState("");
    const [isSaving, setIsSaving] = useState(false);

    const isDirty = useMemo(() => {
        const pendingVars = newKey && newValue ? { ...contextVars, [newKey]: newValue } : contextVars;
        return JSON.stringify(pendingVars) !== JSON.stringify(templateContextVariables);
    }, [contextVars, newKey, newValue, templateContextVariables]);

    useUnsavedChanges("variables", isDirty);

    const handleAdd = () => {
        if (newKey && newValue) {
            setContextVars((prev) => ({ ...prev, [newKey]: newValue }));
        }
        setNewKey("");
        setNewValue("");
    };

    const handleRemove = (key: string) => {
        setContextVars((prev) => {
            const next = { ...prev };
            delete next[key];
            return next;
        });
    };

    const handleSave = async () => {
        setIsSaving(true);
        try {
            let varsToSave = contextVars;
            if (newKey && newValue) {
                varsToSave = { ...varsToSave, [newKey]: newValue };
            }
            await onSave(varsToSave);
        } catch (error) {
            console.error("Failed to save variables:", error);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Card id="variables">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <Variable className="h-4 w-4" />
                    Template Variables
                </CardTitle>
                <CardDescription>
                    Variables available in workflow prompts via {`{{variable_name}}`} syntax for testing the workflow.{" "}
                    <a href={SETTINGS_DOCUMENTATION_URLS.templateVariables} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">Learn more <ExternalLink className="h-3 w-3" /></a>
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                {/* Existing Variables */}
                {Object.entries(contextVars).length > 0 && (
                    <div className="space-y-2">
                        <Label className="text-sm font-medium">Current Variables</Label>
                        {Object.entries(contextVars).map(([key, value]) => (
                            <div key={key} className="flex items-center gap-2 rounded-md border p-2">
                                <div className="flex-1 min-w-0">
                                    <div className="text-sm font-medium">{key}</div>
                                    <div className="text-xs text-muted-foreground truncate">{value}</div>
                                </div>
                                <Button size="sm" variant="ghost" onClick={() => handleRemove(key)}>
                                    <Trash2Icon className="h-4 w-4" />
                                </Button>
                            </div>
                        ))}
                    </div>
                )}

                {/* Add New Variable */}
                <div className="space-y-3">
                    <Label className="text-sm font-medium">Add New Variable</Label>
                    <div className="flex gap-2">
                        <div className="flex-1 space-y-1">
                            <Label htmlFor="var-key" className="text-xs">Key</Label>
                            <Input
                                id="var-key"
                                placeholder="Enter variable key"
                                value={newKey}
                                onChange={(e) => setNewKey(e.target.value)}
                            />
                        </div>
                        <div className="flex-1 space-y-1">
                            <Label htmlFor="var-value" className="text-xs">Value</Label>
                            <Input
                                id="var-value"
                                placeholder="Enter variable value"
                                value={newValue}
                                onChange={(e) => setNewValue(e.target.value)}
                            />
                        </div>
                    </div>
                    <Button size="sm" onClick={handleAdd} disabled={!newKey || !newValue}>
                        Add Variable
                    </Button>
                </div>
            </CardContent>
            <CardFooter className="justify-end gap-3 border-t pt-6">
                {isDirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
                <Button onClick={handleSave} disabled={isSaving || !isDirty}>
                    {isSaving ? "Saving..." : "Save Variables"}
                </Button>
            </CardFooter>
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Section: Call outcomes
// ---------------------------------------------------------------------------

/**
 * The list this agent's finished calls are classified against.
 *
 * On the Analysis tab beside QA, and not the same thing. QA scores how the
 * call was handled; this records what it achieved, and the two come apart
 * constantly — a polite, well-run call to somebody who was never going to buy
 * scores well and books nothing.
 *
 * Editable because the outcomes are a property of the business, not of us. A
 * clinic books appointments, a lending agent gets a payment promise, an NDR
 * agent confirms an address. The defaults are a starting point somebody
 * changes, which is where Vapi and Bolna both land and for the same reason:
 * there is no list that fits everyone.
 */
type ToolChoice = {
    tool_uuid: string;
    name: string;
    category: string;
    parameters: ToolParameter[];
};

function ArgumentsEditor({
    tool,
    values,
    onChange,
}: {
    tool: ToolChoice | undefined;
    values: Record<string, string>;
    onChange: (next: Record<string, string>) => void;
}) {
    const [freeKey, setFreeKey] = useState("");
    if (!tool) return null;

    const declared = tool.parameters;
    const set = (name: string, value: string) => onChange({ ...values, [name]: value });

    return (
        <div className="space-y-2 rounded-md border border-dashed bg-muted/20 p-3">
            {declared.length > 0 ? (
                declared.map((parameter) => (
                    <div key={parameter.name} className="space-y-1">
                        <label className="text-xs font-medium">
                            {parameter.name}
                            {parameter.required ? (
                                <span className="ml-1 text-destructive">*</span>
                            ) : null}
                        </label>
                        <Input
                            className="h-8 font-mono text-xs"
                            placeholder="{{ gathered_context.extracted_variables.customer_name }}"
                            value={values[parameter.name] ?? ""}
                            onChange={(e) => set(parameter.name, e.target.value)}
                        />
                        {parameter.description ? (
                            <p className="text-xs text-muted-foreground">
                                {parameter.description}
                            </p>
                        ) : null}
                    </div>
                ))
            ) : (
                <>
                    <p className="text-xs text-muted-foreground">
                        This tool does not declare what it needs, so name the fields
                        yourself.
                    </p>
                    {Object.entries(values).map(([name, value]) => (
                        <div key={name} className="grid grid-cols-[1fr_1.6fr_auto] gap-2">
                            <Input className="h-8 text-xs" value={name} disabled />
                            <Input
                                className="h-8 font-mono text-xs"
                                value={value}
                                onChange={(e) => set(name, e.target.value)}
                            />
                            <Button
                                variant="ghost"
                                size="icon"
                                aria-label={`Remove ${name}`}
                                onClick={() => {
                                    const next = { ...values };
                                    delete next[name];
                                    onChange(next);
                                }}
                            >
                                <Trash2 className="h-4 w-4" />
                            </Button>
                        </div>
                    ))}
                    <div className="flex gap-2">
                        <Input
                            className="h-8 text-xs"
                            placeholder="Field name"
                            value={freeKey}
                            onChange={(e) => setFreeKey(e.target.value)}
                        />
                        <Button
                            variant="outline"
                            size="sm"
                            disabled={!freeKey.trim()}
                            onClick={() => {
                                set(freeKey.trim(), "");
                                setFreeKey("");
                            }}
                        >
                            Add field
                        </Button>
                    </div>
                </>
            )}

            <div className="flex flex-wrap items-center gap-1 pt-1">
                <span className="text-xs text-muted-foreground">Available:</span>
                {ALWAYS_AVAILABLE.map((variable) => (
                    <code
                        key={variable.token}
                        title={variable.label}
                        className="rounded bg-muted px-1 py-0.5 text-[10px]"
                    >
                        {variable.token}
                    </code>
                ))}
            </div>
            <p className="text-xs text-muted-foreground">
                Anything your agent collects is at{" "}
                <code className="text-[10px]">
                    {"{{ gathered_context.extracted_variables.your_field }}"}
                </code>
                .
            </p>
        </div>
    );
}

/** Tool kinds that can run once the call is over.
 *
 * Ending a call that has ended and transferring a caller who has hung up are
 * not options, so they are not offered. Mirrors RUNNABLE_KINDS in
 * api/services/workflow/outcomes.py -- if that list grows, this one has to.
 */
const RUNNABLE_TOOL_KINDS = ["http_api", "composio", "google_calendar"];

function OutcomeActionsSection({
    actions,
    outcomes,
    onSave,
}: {
    actions: OutcomeAction[];
    outcomes: CallOutcome[];
    onSave: (actions: OutcomeAction[]) => Promise<void>;
}) {
    const [rows, setRows] = useState<OutcomeAction[]>(actions);
    const [tools, setTools] = useState<ToolChoice[]>([]);
    const [isSaving, setIsSaving] = useState(false);

    const isDirty = JSON.stringify(rows) !== JSON.stringify(actions);
    useUnsavedChanges("outcome-actions", isDirty);

    useEffect(() => {
        let cancelled = false;
        listToolsApiV1ToolsGet()
            .then((response) => {
                if (cancelled) return;
                const usable = (response.data ?? [])
                    .filter((tool) => RUNNABLE_TOOL_KINDS.includes(tool.category ?? ""))
                    .map((tool) => ({
                        tool_uuid: tool.tool_uuid ?? "",
                        name: tool.name ?? "Untitled tool",
                        category: tool.category ?? "",
                        parameters: toolParameters(tool.definition),
                    }));
                setTools(usable);
            })
            .catch(() => setTools([]));
        return () => {
            cancelled = true;
        };
    }, []);

    const update = (index: number, patch: Partial<OutcomeAction>) =>
        setRows((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));

    const handleSave = async () => {
        setIsSaving(true);
        try {
            await onSave(rows.filter((row) => row.tool_uuid));
        } catch (error) {
            console.error("Failed to save outcome actions:", error);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Card id="outcome-actions">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <CheckCircle2 className="h-4 w-4" />
                    After the call
                </CardTitle>
                <CardDescription>
                    What should happen once a call is finished &mdash; file the booking
                    in a sheet, raise the record, send the link. This runs after the
                    caller has hung up, so it never makes anybody wait, and it runs
                    every time rather than when the agent decides to.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
                {tools.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                        Nothing to run yet. Connect an app under{" "}
                        <Link href="/integrations/apps" className="underline">
                            Integrations
                        </Link>{" "}
                        and it will appear here.
                    </p>
                ) : null}

                {rows.map((row, index) => (
                    <div
                        key={index}
                        className="grid grid-cols-[1.4fr_1.2fr_auto] gap-2 items-start"
                    >
                        <select
                            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                            value={row.tool_uuid}
                            onChange={(e) => update(index, { tool_uuid: e.target.value })}
                        >
                            <option value="">Choose what to do…</option>
                            {tools.map((tool) => (
                                <option key={tool.tool_uuid} value={tool.tool_uuid}>
                                    {tool.name}
                                </option>
                            ))}
                        </select>

                        <select
                            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                            value={row.when?.[0] ?? ""}
                            onChange={(e) =>
                                update(index, {
                                    when: e.target.value ? [e.target.value] : [],
                                })
                            }
                        >
                            {/* Empty means every call, and it is first because it is
                                the right answer for "log it" -- a missing row is
                                easier to notice than an invoice sent to somebody who
                                did not buy anything. */}
                            <option value="">On every call</option>
                            {outcomes.map((outcome) => (
                                <option key={outcome.code} value={outcome.code}>
                                    Only when {outcome.label || outcome.code}
                                </option>
                            ))}
                        </select>

                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={() =>
                                setRows((prev) => prev.filter((_, i) => i !== index))
                            }
                            aria-label="Remove this step"
                        >
                            <Trash2 className="h-4 w-4" />
                        </Button>

                        {/* Under the row it belongs to, spanning the grid, so a
                            step with six fields still reads as one step. */}
                        {row.tool_uuid ? (
                            <div className="col-span-3">
                                <ArgumentsEditor
                                    tool={tools.find((t) => t.tool_uuid === row.tool_uuid)}
                                    values={(row.arguments ?? {}) as Record<string, string>}
                                    onChange={(next) => update(index, { arguments: next })}
                                />
                            </div>
                        ) : null}
                    </div>
                ))}

                <div className="flex items-center gap-2 pt-1">
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={tools.length === 0}
                        onClick={() =>
                            setRows((prev) => [
                                ...prev,
                                { tool_uuid: "", when: [], arguments: {}, enabled: true },
                            ])
                        }
                    >
                        <Plus className="mr-1 h-4 w-4" />
                        Add a step
                    </Button>
                    <Button size="sm" onClick={handleSave} disabled={!isDirty || isSaving}>
                        {isSaving ? "Saving…" : "Save"}
                    </Button>
                </div>
            </CardContent>
        </Card>
    );
}


function CallOutcomesSection({
    outcomes,
    onSave,
}: {
    outcomes: CallOutcome[];
    onSave: (outcomes: CallOutcome[]) => Promise<void>;
}) {
    // An agent that has never been configured is already classifying against
    // the defaults, so that is what the editor shows — not an empty list
    // implying nothing happens.
    const stored = outcomes.length > 0 ? outcomes : [...DEFAULT_CALL_OUTCOMES];
    const [rows, setRows] = useState<CallOutcome[]>(stored);
    const [isSaving, setIsSaving] = useState(false);

    const isDirty = JSON.stringify(rows) !== JSON.stringify(stored);
    useUnsavedChanges("outcomes", isDirty);

    const update = (index: number, field: keyof CallOutcome, value: string) =>
        setRows((prev) =>
            prev.map((row, i) => (i === index ? { ...row, [field]: value } : row)),
        );

    // Shown under the row as it is typed rather than rewritten on save: a
    // code is what lands in a CRM field and a CSV heading, and finding out
    // afterwards that "Call Back" became `call_back` is a surprise.
    const codeHint = (raw: string) => {
        const normalised = normaliseOutcomeCode(raw);
        if (!raw.trim()) return null;
        if (!normalised) return "Needs to start with a letter";
        return normalised === raw ? null : `Saved as ${normalised}`;
    };

    const handleSave = async () => {
        setIsSaving(true);
        try {
            await onSave(outcomesToSave(rows));
        } catch (error) {
            console.error("Failed to save call outcomes:", error);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Card id="outcomes">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <Tags className="h-4 w-4" />
                    Call outcomes
                </CardTitle>
                <CardDescription>
                    What you want to sort finished calls by. Every call gets one or
                    more of these after it ends &mdash; a call that books and also
                    asks for a follow-up is both. Separate from how the call ended,
                    which is recorded either way.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
                <div className="grid grid-cols-[1fr_1.6fr_auto] gap-2 text-xs text-muted-foreground">
                    <span>Name</span>
                    <span>Use it when</span>
                    <span className="w-8" />
                </div>
                {rows.map((row, index) => {
                    const locked = row.code === UNCLEAR_OUTCOME.code;
                    const hint = codeHint(row.label || row.code);
                    return (
                        <div key={index} className="space-y-1">
                            <div className="grid grid-cols-[1fr_1.6fr_auto] gap-2">
                                <Input
                                    value={row.label || row.code}
                                    disabled={locked}
                                    onChange={(e) => {
                                        update(index, "label", e.target.value);
                                        update(index, "code", e.target.value);
                                    }}
                                    placeholder="Booked"
                                />
                                <Input
                                    value={row.when}
                                    disabled={locked}
                                    onChange={(e) => update(index, "when", e.target.value)}
                                    placeholder="An appointment is confirmed"
                                />
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    disabled={locked}
                                    aria-label={`Remove ${row.label || row.code || "outcome"}`}
                                    onClick={() =>
                                        setRows((prev) => prev.filter((_, i) => i !== index))
                                    }
                                >
                                    <Trash2 className="h-4 w-4" />
                                </Button>
                            </div>
                            {locked ? (
                                <p className="text-xs text-muted-foreground">
                                    Always available. Without somewhere to put a call it
                                    genuinely cannot read, the classifier picks the nearest
                                    real outcome instead.
                                </p>
                            ) : (
                                hint && <p className="text-xs text-muted-foreground">{hint}</p>
                            )}
                        </div>
                    );
                })}
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={rows.length >= MAX_CALL_OUTCOMES}
                    onClick={() =>
                        setRows((prev) => [
                            // Before `unclear`, which stays at the bottom.
                            ...prev.filter((r) => r.code !== UNCLEAR_OUTCOME.code),
                            { code: "", label: "", when: "" },
                            ...prev.filter((r) => r.code === UNCLEAR_OUTCOME.code),
                        ])
                    }
                >
                    <Plus className="mr-1 h-4 w-4" />
                    Add an outcome
                </Button>
                {rows.length >= MAX_CALL_OUTCOMES && (
                    <p className="text-xs text-muted-foreground">
                        {MAX_CALL_OUTCOMES} is the limit. Past that this is not a list
                        anyone sorts by, and each one is sent with every transcript.
                    </p>
                )}
            </CardContent>
            <CardFooter className="justify-end gap-3 border-t pt-6">
                {isDirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
                <Button onClick={handleSave} disabled={isSaving || !isDirty}>
                    {isSaving ? "Saving..." : "Save Outcomes"}
                </Button>
            </CardFooter>
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Section: Voicemail Detection
// ---------------------------------------------------------------------------

function VoicemailSection({
    workflowConfigurations,
    workflowName,
    onSave,
}: {
    workflowConfigurations: WorkflowConfigurations;
    workflowName: string;
    onSave: (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;
}) {
    const getConfig = (): VoicemailDetectionConfiguration => ({
        ...DEFAULT_VOICEMAIL_DETECTION_CONFIGURATION,
        ...workflowConfigurations.voicemail_detection,
    });

    const [enabled, setEnabled] = useState(getConfig().enabled);
    const [useWorkflowLlm, setUseWorkflowLlm] = useState(getConfig().use_workflow_llm);
    const [provider, setProvider] = useState(getConfig().provider || "openai");
    const [model, setModel] = useState(getConfig().model || "gpt-4.1");
    const [apiKey, setApiKey] = useState(getConfig().api_key || "");
    const [systemPrompt, setSystemPrompt] = useState(getConfig().system_prompt || DEFAULT_VOICEMAIL_SYSTEM_PROMPT);
    const [longSpeechTimeout, setLongSpeechTimeout] = useState(getConfig().long_speech_timeout);
    const [isSaving, setIsSaving] = useState(false);

    const isDirty = useMemo(() => {
        const init = {
            ...DEFAULT_VOICEMAIL_DETECTION_CONFIGURATION,
            ...workflowConfigurations.voicemail_detection,
        };
        return (
            enabled !== init.enabled ||
            useWorkflowLlm !== init.use_workflow_llm ||
            provider !== (init.provider || "openai") ||
            model !== (init.model || "gpt-4.1") ||
            apiKey !== (init.api_key || "") ||
            systemPrompt !== (init.system_prompt || DEFAULT_VOICEMAIL_SYSTEM_PROMPT) ||
            longSpeechTimeout !== init.long_speech_timeout
        );
    }, [enabled, useWorkflowLlm, provider, model, apiKey, systemPrompt, longSpeechTimeout, workflowConfigurations]);

    useUnsavedChanges("voicemail", isDirty);

    const handleSave = async () => {
        setIsSaving(true);
        try {
            const voicemailConfig: VoicemailDetectionConfiguration = {
                enabled,
                use_workflow_llm: useWorkflowLlm,
                provider: useWorkflowLlm ? undefined : provider,
                model: useWorkflowLlm ? undefined : model,
                api_key: useWorkflowLlm ? undefined : apiKey,
                system_prompt:
                    systemPrompt && systemPrompt !== DEFAULT_VOICEMAIL_SYSTEM_PROMPT ? systemPrompt : undefined,
                long_speech_timeout: longSpeechTimeout,
            };
            await onSave(
                { ...workflowConfigurations, voicemail_detection: voicemailConfig },
                workflowName,
            );
        } catch (error) {
            console.error("Failed to save voicemail settings:", error);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Card id="voicemail">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <PhoneOff className="h-4 w-4" />
                    Voicemail Detection
                </CardTitle>
                <CardDescription>
                    Automatically detect and end calls when a voicemail system is reached.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="flex items-center space-x-2 rounded-md border bg-muted/20 p-2">
                    <Switch id="voicemail-enabled" checked={enabled} onCheckedChange={setEnabled} />
                    <Label htmlFor="voicemail-enabled">Enable Voicemail Detection</Label>
                </div>

                {enabled && (
                    <>
                        {/* LLM Configuration */}
                        <div className="space-y-3">
                            <div className="flex items-center space-x-2 rounded-md border bg-muted/20 p-2">
                                <Switch
                                    id="voicemail-use-workflow-llm"
                                    checked={useWorkflowLlm}
                                    onCheckedChange={setUseWorkflowLlm}
                                />
                                <Label htmlFor="voicemail-use-workflow-llm">Use Workflow LLM</Label>
                                <Label className="ml-2 text-xs text-muted-foreground">
                                    Use the LLM configured in your account settings.
                                </Label>
                            </div>

                            {!useWorkflowLlm && (
                                <LLMConfigSelector
                                    provider={provider}
                                    onProviderChange={setProvider}
                                    model={model}
                                    onModelChange={setModel}
                                    apiKey={apiKey}
                                    onApiKeyChange={setApiKey}
                                />
                            )}
                        </div>

                        {/* System Prompt */}
                        <div className="space-y-2">
                            <Label>System Prompt</Label>
                            <p className="text-xs text-muted-foreground">
                                The LLM must respond with either &quot;CONVERSATION&quot; or &quot;VOICEMAIL&quot;.
                            </p>
                            <Textarea
                                value={systemPrompt}
                                onChange={(e) => setSystemPrompt(e.target.value)}
                                className="min-h-[200px] font-mono text-xs"
                            />
                        </div>

                        {/* Timing */}
                        <div className="space-y-2 rounded-md border bg-muted/10 p-3">
                            <Label className="font-medium">Timing</Label>
                            <div className="space-y-2">
                                <Label className="text-sm">Speech Cutoff (seconds)</Label>
                                <p className="text-xs text-muted-foreground">
                                    Trigger classification early if first turn speech exceeds this duration.
                                </p>
                                <Input
                                    type="number"
                                    step="0.5"
                                    min="1"
                                    max="30"
                                    value={longSpeechTimeout}
                                    onChange={(e) => setLongSpeechTimeout(parseFloat(e.target.value) || 8.0)}
                                />
                            </div>
                        </div>
                    </>
                )}
            </CardContent>
            <CardFooter className="justify-end gap-3 border-t pt-6">
                {isDirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
                <Button onClick={handleSave} disabled={isSaving || !isDirty}>
                    {isSaving ? "Saving..." : "Save Voicemail Settings"}
                </Button>
            </CardFooter>
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Section: Agent UUID
// ---------------------------------------------------------------------------

function AgentUuidSection({ workflowUuid }: { workflowUuid: string }) {
    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(workflowUuid);
            toast.success("Agent UUID copied");
        } catch {
            toast.error("Failed to copy Agent UUID");
        }
    };

    return (
        <Card id="identity">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <Fingerprint className="h-4 w-4" />
                    Agent UUID
                </CardTitle>
                <CardDescription>
                    Stable identifier for this agent. Used in agent-stream URLs and
                    other integrations where a numeric workflow ID isn&apos;t portable.
                </CardDescription>
            </CardHeader>
            <CardContent>
                <button
                    type="button"
                    onClick={handleCopy}
                    title="Click to copy"
                    className="group flex w-full items-center gap-2 rounded-md border bg-muted/20 p-2 text-left font-mono text-xs transition-colors hover:bg-muted/40"
                >
                    <code className="flex-1 truncate">{workflowUuid}</code>
                    <Clipboard className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-colors group-hover:text-foreground" />
                </button>
            </CardContent>
            <CardFooter className="border-t pt-6">
                <Button variant="outline" size="sm" onClick={handleCopy}>
                    <Clipboard className="h-3.5 w-3.5 mr-2" />
                    Copy UUID
                </Button>
            </CardFooter>
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Section: Model Overrides
// ---------------------------------------------------------------------------
// Page wrapper — handles auth & data fetching, then mounts the content
// component only when everything is loaded. This avoids useWorkflowState
// running with empty initial values and overwriting the Zustand store.
// ---------------------------------------------------------------------------

export default function WorkflowSettingsPage() {
    const params = useParams();
    const { user, redirectToLogin, loading: authLoading } = useAuth();
    const [workflow, setWorkflow] = useState<WorkflowResponse | undefined>(undefined);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!authLoading && !user) {
            redirectToLogin();
        }
    }, [authLoading, user, redirectToLogin]);

    useEffect(() => {
        const fetchWorkflow = async () => {
            if (!user) return;
            try {
                const response = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({
                    path: { workflow_id: Number(params.workflowId) },
                });
                setWorkflow(response.data);
            } catch (err) {
                setError("Failed to fetch workflow");
                logger.error(`Error fetching workflow settings: ${err}`);
            } finally {
                setLoading(false);
            }
        };
        if (user) fetchWorkflow();
    }, [params.workflowId, user]);

    if (loading || authLoading) return <SpinLoader />;

    if (error || !workflow) {
        return (
            <div className="flex min-h-screen items-center justify-center">
                <div className="text-lg text-destructive">{error || "Workflow not found"}</div>
            </div>
        );
    }

    if (!user) return null;

    return <WorkflowSettingsContent workflow={workflow} user={user} />;
}

// ---------------------------------------------------------------------------
// Content — only mounts once the workflow API response is available, so
// useWorkflowState always initialises with real data.
// ---------------------------------------------------------------------------

function WorkflowSettingsContent({
    workflow,
    user,
}: {
    workflow: WorkflowResponse;
    user: { id: string; email?: string };
}) {
    return (
        <UnsavedChangesProvider>
            <WorkflowSettingsInner workflow={workflow} user={user} />
        </UnsavedChangesProvider>
    );
}

function WorkflowSettingsInner({
    workflow,
    user,
}: {
    workflow: WorkflowResponse;
    user: { id: string; email?: string };
}) {
    const { dirtySections, confirmNavigate } = useUnsavedChangesContext();

    // Which tabs are hiding an edit nobody has saved. Derived from the section
    // map rather than tracked separately: `tabs.ts` already decides what lives
    // where, and a second list of that would be the one that goes stale.
    const dirtyTabIds = useMemo(
        () =>
            new Set(
                TABS.filter((tab) =>
                    tab.sections.some((id) => dirtySections.has(id)),
                ).map((tab) => tab.id),
            ),
        [dirtySections],
    );

    // Read from the URL on every render, not once on mount. The tabs are links
    // now, and a link to this same route with a different `?tab=` does not
    // remount the page — so a value captured in an effect would leave the URL
    // saying "calling" and the screen still showing Models. That is also what
    // makes a link land on a tab at all, which the wizard's "Advanced setup"
    // and the docs both rely on.
    const requestedTab = useSearchParams().get("tab");
    const activeTab: TabId = isTabId(requestedTab) ? requestedTab : DEFAULT_TAB;
    // Only the fallback chains read this now: the schemas say which vendors
    // a backup voice or transcriber can name.
    const [modelConfigurationDefaults, setModelConfigurationDefaults] = useState<ModelConfigurationDefaultsV2 | null>(null);
    const hasFetchedModelConfiguration = useRef(false);

    const workflowId = workflow.id;

    const initialFlow = useMemo(
        () => ({
            nodes: workflow.workflow_definition.nodes as FlowNode[],
            edges: workflow.workflow_definition.edges as FlowEdge[],
            viewport: { x: 0, y: 0, zoom: 0 },
        }),
        [workflow],
    );

    const initialTemplateContextVariables = useMemo(
        () => (workflow.template_context_variables as Record<string, string>) || {},
        [workflow],
    );

    const initialWorkflowConfigurations = useMemo(
        () => (
            workflow.workflow_configurations
                ? (workflow.workflow_configurations as WorkflowConfigurations)
                : undefined
        ),
        [workflow],
    );

    const {
        workflowName,
        workflowConfigurations,
        templateContextVariables,
        saveWorkflowConfigurations,
        saveTemplateContextVariables,
        saveCallOutcomes,
        saveOutcomeActions,
    } = useWorkflowState({
        initialWorkflowName: workflow.name,
        workflowId,
        initialFlow,
        initialTemplateContextVariables,
        initialWorkflowConfigurations,
        user,
    });
    const resolvedWorkflowConfigurationsForRender = workflowConfigurations
        ? resolveWorkflowConfigurations(workflowConfigurations)
        : null;

    useEffect(() => {
        if (hasFetchedModelConfiguration.current) return;
        hasFetchedModelConfiguration.current = true;

        const loadModelConfiguration = async () => {
            const defaultsResult =
                await getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet();
            if (defaultsResult.error) {
                logger.error("Failed to load model configuration defaults");
                return;
            }
            setModelConfigurationDefaults(defaultsResult.data as ModelConfigurationDefaultsV2);
        };

        loadModelConfiguration();
    }, []);

    return (
        <div className="min-h-screen">
            <AgentHeader
                workflowId={workflowId}
                name={workflowName || workflow.name}
                onBack={confirmNavigate}
            />

            {/* The only strip. It used to be the outer of two, with Analysis
                and Advanced appearing in both a centimetre apart and going to
                different places. Four of its tabs are this page, so the page
                says which one is showing and which are hiding an unsaved
                edit — a tab that hides one without saying so is how somebody
                loses work. */}
            <AgentTabs
                workflowId={workflowId}
                settingsTab={activeTab}
                dirtyTabs={dirtyTabIds}
            />

            <div className="mx-auto max-w-4xl px-6 py-8">
                <div className="min-w-0 space-y-8">
                    {resolvedWorkflowConfigurationsForRender && (
                        <>
                            <div
                                className={cn("space-y-8", activeTab !== "analysis" && "hidden")}
                            >
                            {/* Whether calls get reviewed at all. First on the
                                tab because it is the reason the tab used to
                                look empty: QA has been built for months and
                                ran on almost nothing, since no creation path
                                added the node the runtime looks for. Every
                                path does now, so for a new agent this reads as
                                on; it stays the control for older ones. */}
                            <QaCard workflowId={workflowId} />

                            {/* Under QA because it is the other half of the
                                same screen: QA says how the call was handled,
                                this says what it achieved, and a well-handled
                                call that books nothing scores well on one and
                                badly on the other. */}
                            <CallOutcomesSection
                                outcomes={workflowConfigurations?.call_outcomes ?? []}
                                onSave={saveCallOutcomes}
                            />

                            {/* Directly under the taxonomy it keys off. One says
                                what a call can turn out to be, the other what
                                happens when it does, and reading them apart is
                                how somebody configures a step that never fires. */}
                            <OutcomeActionsSection
                                actions={workflowConfigurations?.outcome_actions ?? []}
                                outcomes={workflowConfigurations?.call_outcomes ?? []}
                                onSave={saveOutcomeActions}
                            />

                            {/* Recordings – moved to org-level page */}
                            <Card id="recordings">
                                <CardHeader>
                                    <CardTitle className="flex items-center gap-2 text-base">
                                        <Mic className="h-4 w-4" />
                                        Recordings
                                    </CardTitle>
                                    <CardDescription>
                                        Recordings are now managed at the organization level and shared across all agents.
                                        Use <code className="rounded bg-muted px-1 text-xs">@</code> in prompt fields to insert them.{" "}
                                        <a href={SETTINGS_DOCUMENTATION_URLS.recordings} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">Learn more <ExternalLink className="h-3 w-3" /></a>
                                    </CardDescription>
                                </CardHeader>
                                <CardFooter className="border-t pt-6">
                                    <Button variant="outline" asChild>
                                        <Link href="/recordings">
                                            Go to Recordings
                                            <ExternalLink className="ml-2 h-4 w-4" />
                                        </Link>
                                    </Button>
                                </CardFooter>
                            </Card>

                            {/* Report */}
                            <ReportSection workflowId={workflowId} />

                            {/* Evals have their own screen; this is the way
                                to it now that the tab strip is Vapi's five
                                and Share. Under Analysis because that is
                                what an eval is: a judgement on calls. */}
                            <Card id="evals">
                                <CardHeader>
                                    <CardTitle className="flex items-center gap-2 text-base">
                                        <FlaskConical className="h-4 w-4" />
                                        Evals
                                    </CardTitle>
                                    <CardDescription>
                                        Scripted conversations this agent is graded against, run before a change goes live.
                                    </CardDescription>
                                </CardHeader>
                                <CardFooter className="border-t pt-6">
                                    <Button variant="outline" asChild>
                                        <Link href={`/workflow/${workflowId}/evals`}>
                                            Open Evals
                                            <ExternalLink className="ml-2 h-4 w-4" />
                                        </Link>
                                    </Button>
                                </CardFooter>
                            </Card>
                            </div>

                            <div
                                className={cn("space-y-8", activeTab !== "share" && "hidden")}
                            >
                            {/* The two ways this agent reaches people who are
                                not in this account: a link to text a prospect,
                                and a widget for the website. */}
                            <Card id="share">
                                <CardHeader>
                                    <CardTitle className="flex items-center gap-2 text-base">
                                        <Share2 className="h-4 w-4" />
                                        Shareable link
                                    </CardTitle>
                                    <CardDescription>
                                        One link, no account: whoever opens it talks to this agent in the browser, with live captions.
                                    </CardDescription>
                                </CardHeader>
                                <CardFooter className="border-t pt-6">
                                    <ShareAgentDialog workflowId={workflowId} />
                                </CardFooter>
                            </Card>

                            <Card id="deployment">
                                <CardHeader>
                                    <CardTitle className="flex items-center gap-2 text-base">
                                        <Rocket className="h-4 w-4" />
                                        Add to website
                                    </CardTitle>
                                    <CardDescription>
                                        Configure a widget to add this voice agent to your website.{" "}
                                        <a href={SETTINGS_DOCUMENTATION_URLS.deployment} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">Learn more <ExternalLink className="h-3 w-3" /></a>
                                    </CardDescription>
                                </CardHeader>
                                <CardFooter className="border-t pt-6">
                                    {/* A link, not a modal. The configurator now
                                        has its own screen under DEPLOY, where
                                        somebody who did not build this agent can
                                        find it. ?agent= carries the choice across
                                        so arriving from here skips the picker. */}
                                    <Button variant="outline" asChild>
                                        <Link href={`/deploy/web-widget?agent=${workflowId}`}>
                                            Configure Widget
                                            <ExternalLink className="ml-2 h-4 w-4" />
                                        </Link>
                                    </Button>
                                </CardFooter>
                            </Card>
                            </div>

                            <div
                                className={cn("space-y-8", activeTab !== "advanced" && "hidden")}
                            >
                            {/* Calls: name, fallbacks, limits, recording. The
                                turn-taking and audio settings that used to sit
                                here are behind the model tiles now. */}
                            <GeneralSection
                                workflowConfigurations={resolvedWorkflowConfigurationsForRender}
                                workflowName={workflowName || workflow.name}
                                onSave={saveWorkflowConfigurations}
                                modelConfigurationDefaults={modelConfigurationDefaults}
                            />

                            <VoicemailSection
                                workflowConfigurations={resolvedWorkflowConfigurationsForRender}
                                workflowName={workflowName}
                                onSave={saveWorkflowConfigurations}
                            />

                            {/* Template Variables */}
                            <TemplateVariablesSection
                                templateContextVariables={templateContextVariables}
                                onSave={saveTemplateContextVariables}
                            />

                            {/* Agent UUID */}
                            {workflow.workflow_uuid && (
                                <AgentUuidSection workflowUuid={workflow.workflow_uuid} />
                            )}
                            </div>
                        </>
                    )}
                </div>

            </div>

        </div>
    );
}
