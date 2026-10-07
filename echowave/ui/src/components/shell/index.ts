/**
 * The shared components named in the design handoff ("Shared components"),
 * in one place. EmptyState is the app's existing one, re-exported rather
 * than duplicated.
 */
export { ActionPreview, type ActionPreviewData, type ApprovalStatus } from "./ActionPreview";
export { Announcer } from "./Announcer";
export { AuditTimeline, type AuditTimelineEntry } from "./AuditTimeline";
export { CommandPreview, type CommandState, type OperationalCommand } from "./CommandPreview";
export { type CapabilityState, ConnectionRow } from "./ConnectionRow";
export { ErrorState } from "./ErrorState";
export { MetricDefinition } from "./MetricDefinition";
export { SaveBar, type SaveState } from "./SaveBar";
export { ScopedSearch, type SearchScope } from "./ScopedSearch";
export { SettingsSection } from "./SettingsSection";
export { coverageLine, SourceCoverage } from "./SourceCoverage";
export { TaskStatus } from "./TaskStatus";
export { EmptyState } from "@/components/EmptyState";
