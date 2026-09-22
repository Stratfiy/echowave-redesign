import { BaseEdge, EdgeLabelRenderer, type EdgeProps, getSmoothStepPath } from '@xyflow/react';
import { AlertCircle, Trash2 } from 'lucide-react';
import { useCallback, useId, useRef, useState } from 'react';

import { useWorkflow, useWorkflowOptional } from '@/app/workflow/[workflowId]/contexts/WorkflowContext';
import { useWorkflowStore } from '@/app/workflow/[workflowId]/stores/workflowStore';
import { StaticTextWarning, TextOrAudioInput } from '@/components/flow/TextOrAudioInput';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

import { FlowEdgeData } from '../types';

interface CustomEdgeProps extends EdgeProps { data: FlowEdgeData }

export default function CustomEdge(props: CustomEdgeProps) {
    const { id, source, target, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, style, selected, markerEnd, markerStart } = props;
    const readOnly = useWorkflowOptional()?.readOnly ?? false;
    const { recordings } = useWorkflow();
    const updateEdge = useWorkflowStore(state => state.updateEdge);
    const deleteEdge = useWorkflowStore(state => state.deleteEdge);
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState<FlowEdgeData>(data);
    const fieldId = useId();
    const labelRef = useRef<HTMLInputElement>(null);
    const openerRef = useRef<HTMLButtonElement>(null);
    const edges = useWorkflowStore(state => state.edges);
    const parallel = edges.filter(edge => (edge.source === source && edge.target === target) || (edge.source === target && edge.target === source)).sort((a, b) => a.id.localeCompare(b.id));
    const labelOffset = parallel.length > 1 ? (parallel.findIndex(edge => edge.id === id) - (parallel.length - 1) / 2) * 30 : 0;
    // Seeded when the inspector is opened, and only then. It used to re-seed
    // on every change of `data` identity, and opening is not the only thing
    // that changes it: marking the graph invalid rewrites the data of every
    // edge, so a validation pass landing while this was open threw away
    // whatever was being typed into it.
    const openInspector = useCallback(() => { setDraft({ ...data }); setOpen(true); }, [data]);

    const apply = () => {
        if (readOnly) return;
        // Update the shared draft and its undo history; saving remains explicit.
        const next = { ...data, ...draft };
        if ((next.transition_speech_type ?? 'text') === 'audio') next.transition_speech = '';
        else next.transition_speech_recording_id = '';
        updateEdge(id, { data: next });
        setOpen(false);
    };
    const [path, labelX, labelY] = source === target
        ? [`M ${sourceX} ${sourceY} C ${sourceX + 80} ${sourceY - 50}, ${targetX + 80} ${targetY + 50}, ${targetX} ${targetY}`, sourceX + 80, sourceY]
        : getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 8, offset: 20 });
    const speechType = draft?.transition_speech_type ?? 'text';
    return <>
        <g onDoubleClick={openInspector}>
            <BaseEdge id={id} path={path} markerEnd={markerEnd} markerStart={markerStart} interactionWidth={20}
                style={{ ...style, stroke: data?.invalid ? '#ef4444' : selected ? '#0d9488' : '#94a3b8', strokeWidth: selected ? 2 : 1.5 }} />
        </g>
        <EdgeLabelRenderer>
            <button ref={openerRef} type="button" aria-label={`Edit connection: ${data?.label || 'Condition'}`}
                style={{ position: 'absolute', pointerEvents: 'all', transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY + labelOffset}px)` }}
                onClick={openInspector}
                className={cn('nodrag nopan flex max-w-[144px] items-center gap-1 rounded-md border bg-background px-2 py-1 text-[11px] font-medium shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500', data?.invalid ? 'border-destructive text-destructive' : selected ? 'border-teal-600 text-teal-700 dark:text-teal-300' : 'border-border text-muted-foreground hover:border-teal-600')}>
                {data?.invalid && <AlertCircle aria-label="Invalid connection" className="size-3 shrink-0" />}
                <span className="truncate">{data?.label || 'Condition'}</span>
            </button>
        </EdgeLabelRenderer>
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogContent style={{ '--primary': '#0f766e', '--primary-foreground': '#ffffff', '--ring': '#0f766e' } as React.CSSProperties} onOpenAutoFocus={event => { event.preventDefault(); labelRef.current?.focus(); }} onCloseAutoFocus={event => { event.preventDefault(); openerRef.current?.focus(); }} className="left-0 right-0 top-auto bottom-0 flex max-h-[85dvh] w-full max-w-none translate-x-0 translate-y-0 flex-col rounded-t-xl rounded-b-none sm:left-auto sm:top-0 sm:bottom-0 sm:h-dvh sm:max-h-none sm:w-[400px] sm:max-w-[90vw] sm:rounded-none"
                onKeyDownCapture={event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); event.stopPropagation(); apply(); } }}>
                <DialogHeader>
                    <DialogTitle>Connection</DialogTitle>
                    <DialogDescription>Choose when this path runs. Apply updates your draft; use the editor Save button to keep changes.</DialogDescription>
                </DialogHeader>
                {data?.invalid && data.validationMessage && <p role="alert" className="rounded-md border border-destructive/30 p-2 text-sm text-destructive">{data.validationMessage}</p>}
                <fieldset disabled={readOnly} className="grid min-h-0 gap-4 overflow-y-auto py-2">
                    <div className="grid gap-2">
                        <Label htmlFor={`${fieldId}-label`}>Label</Label>
                        <p className="text-xs text-muted-foreground">A short name for this path, also shown in logs.</p>
                        <Input ref={labelRef} id={`${fieldId}-label`} value={draft?.label ?? ''} maxLength={64} onChange={event => setDraft({ ...draft, label: event.target.value })} />
                    </div>
                    <div className="grid gap-2">
                        <Label htmlFor={`${fieldId}-condition`}>Condition</Label>
                        <p className="text-xs text-muted-foreground">Describe when the agent should take this path.</p>
                        <Textarea id={`${fieldId}-condition`} value={draft?.condition ?? ''} onChange={event => setDraft({ ...draft, condition: event.target.value })} />
                    </div>
                    <div className="grid gap-2">
                        <Label>Transition speech</Label>
                        <p className="text-xs text-muted-foreground">Optional speech before moving to the next step. It is not added to conversation context.</p>
                        <TextOrAudioInput type={speechType} onTypeChange={type => setDraft({ ...draft, transition_speech_type: type })}
                            recordingId={draft?.transition_speech_recording_id ?? ''} onRecordingIdChange={recordingId => setDraft({ ...draft, transition_speech_recording_id: recordingId })} recordings={recordings ?? []}>
                            <StaticTextWarning />
                            <Textarea aria-label="Transition speech text" value={draft?.transition_speech ?? ''} onChange={event => setDraft({ ...draft, transition_speech: event.target.value })} />
                        </TextOrAudioInput>
                    </div>
                </fieldset>
                <DialogFooter className="border-t pt-4 sm:justify-between">
                    <Button variant="ghost" className="text-destructive hover:text-destructive" disabled={readOnly} onClick={() => { if (!readOnly) { deleteEdge(id); setOpen(false); } }}><Trash2 className="mr-2 size-4" />Delete connection</Button>
                    <div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button><Button className="bg-teal-700 text-white hover:bg-teal-800" disabled={readOnly} onClick={apply}>{readOnly ? 'Read only' : 'Apply'}</Button></div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    </>;
}
