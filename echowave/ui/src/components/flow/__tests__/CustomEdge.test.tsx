import { fireEvent, render, screen } from '@testing-library/react';
import { Position } from '@xyflow/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import CustomEdge from '../edges/CustomEdge';

const state = vi.hoisted(() => ({ readOnly: false, updateEdge: vi.fn(), deleteEdge: vi.fn(), saveWorkflow: vi.fn(), edges: [] }));
vi.mock('@/app/workflow/[workflowId]/contexts/WorkflowContext', () => ({ useWorkflow: () => ({ recordings: [], saveWorkflow: state.saveWorkflow }), useWorkflowOptional: () => ({ readOnly: state.readOnly }) }));
vi.mock('@/app/workflow/[workflowId]/stores/workflowStore', () => ({ useWorkflowStore: (selector: (value: typeof state) => unknown) => selector(state) }));
vi.mock('@xyflow/react', async importOriginal => ({ ...await importOriginal<typeof import('@xyflow/react')>(), BaseEdge: () => null, EdgeLabelRenderer: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('@/components/flow/TextOrAudioInput', () => ({ StaticTextWarning: () => null, TextOrAudioInput: ({ children, type, onTypeChange }: { children: React.ReactNode; type: string; onTypeChange: (type: string) => void }) => <><button onClick={() => onTypeChange(type === 'text' ? 'audio' : 'text')}>Switch speech mode</button>{type === 'text' ? children : <span>Audio recording selected</span>}</> }));
const data = { label: 'Booking', condition: 'A very long detailed condition that should never fill the canvas', transition_speech_type: 'audio' as const, transition_speech_recording_id: 'recording-7', custom_metadata: { retained: true } };
const props = { id: 'edge-1', source: 'a', target: 'b', sourceX: 0, sourceY: 0, targetX: 100, targetY: 100, sourcePosition: Position.Right, targetPosition: Position.Left, data, selected: true };
function open() { fireEvent.click(screen.getByRole('button', { name: 'Edit connection: Booking' })); }
beforeEach(() => { state.readOnly = false; vi.clearAllMocks(); });
describe('compact edge inspector', () => {
    it('never expands condition on selection or hover; opens only explicitly', () => {
        render(<CustomEdge {...props} />);
        fireEvent.mouseEnter(screen.getByRole('button', { name: 'Edit connection: Booking' }));
        expect(screen.queryByText(data.condition)).toBeNull();
        expect(screen.queryByRole('dialog')).toBeNull();
        open();
        expect(screen.getByRole('dialog', { name: 'Connection' })).toBeTruthy();
        expect(screen.getByLabelText('Condition')).toHaveProperty('value', data.condition);
    });
    it('applies to draft only and preserves audio and unknown metadata', () => {
        render(<CustomEdge {...props} />); open();
        fireEvent.change(screen.getByLabelText('Label'), { target: { value: 'Booked' } });
        fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
        expect(state.updateEdge).toHaveBeenCalledWith('edge-1', { data: { ...data, label: 'Booked', transition_speech: '' } });
        expect(state.saveWorkflow).not.toHaveBeenCalled();
        expect(screen.queryByRole('dialog')).toBeNull();
    });
    it('clears inactive speech fields so hidden text cannot be spoken in audio mode', () => {
        render(<CustomEdge {...props} data={{ ...data, transition_speech_type: 'text', transition_speech: 'Hidden old speech', transition_speech_recording_id: '' }} />); open();
        fireEvent.click(screen.getByRole('button', { name: 'Switch speech mode' }));
        fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
        expect(state.updateEdge.mock.calls[0][1].data).toMatchObject({ transition_speech_type: 'audio', transition_speech: '', custom_metadata: { retained: true } });
    });
    it('clears the old recording when switching to text', () => {
        render(<CustomEdge {...props} />); open();
        fireEvent.click(screen.getByRole('button', { name: 'Switch speech mode' }));
        fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
        expect(state.updateEdge.mock.calls[0][1].data).toMatchObject({ transition_speech_type: 'text', transition_speech_recording_id: '', custom_metadata: { retained: true } });
    });
    it('keyboard apply stays in the draft and does not bubble to global save', () => {
        const globalSave = vi.fn();
        window.addEventListener('keydown', globalSave);
        try {
            render(<CustomEdge {...props} />); open();
            fireEvent.keyDown(screen.getByLabelText('Label'), { key: 's', ctrlKey: true });
            expect(state.updateEdge).toHaveBeenCalledOnce();
            expect(globalSave).not.toHaveBeenCalled();
            expect(state.saveWorkflow).not.toHaveBeenCalled();
        } finally { window.removeEventListener('keydown', globalSave); }
    });
    it('cancels without changing the graph and resets form on reopen', () => {
        render(<CustomEdge {...props} />); open();
        fireEvent.change(screen.getByLabelText('Label'), { target: { value: 'Discard' } });
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); open();
        expect(screen.getByLabelText('Label')).toHaveProperty('value', 'Booking');
        expect(state.updateEdge).not.toHaveBeenCalled();
    });
    it('deletes only through the inspector', () => {
        render(<CustomEdge {...props} />);
        expect(screen.queryByRole('button', { name: 'Delete connection' })).toBeNull(); open();
        fireEvent.click(screen.getByRole('button', { name: 'Delete connection' }));
        expect(state.deleteEdge).toHaveBeenCalledWith('edge-1');
    });
    it('read-only blocks apply, delete and keyboard mutation', () => {
        state.readOnly = true; render(<CustomEdge {...props} />); open();
        expect(screen.getByRole('button', { name: 'Delete connection' })).toHaveProperty('disabled', true);
        expect(screen.getByRole('button', { name: 'Read only' })).toHaveProperty('disabled', true);
        expect(screen.getByLabelText('Label').closest('fieldset')).toHaveProperty('disabled', true);
        fireEvent.keyDown(screen.getByRole('dialog'), { key: 's', ctrlKey: true });
        expect(state.updateEdge).not.toHaveBeenCalled(); expect(state.deleteEdge).not.toHaveBeenCalled();
    });
});
