import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { FlowNode } from '@/components/flow/types';

import { useWorkflowStore } from '../../stores/workflowStore';
import { useWorkflowState } from '../useWorkflowState';

const mocks = vi.hoisted(() => ({ save: vi.fn(), validate: vi.fn(), defaults: vi.fn(), specs: { specs: [], bySpecName: new Map(), loading: false } }));
vi.mock('@/client', () => ({ updateWorkflowApiV1WorkflowWorkflowIdPut: mocks.save, validateWorkflowApiV1WorkflowWorkflowIdValidatePost: mocks.validate, getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet: mocks.defaults }));
vi.mock('@/components/flow/renderer', () => ({ useNodeSpecs: () => mocks.specs }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('posthog-js', () => ({ default: { capture: vi.fn() } }));
vi.mock('@/lib/logger', () => ({ default: { error: vi.fn(), warn: vi.fn() } }));
vi.mock('sonner', () => ({ toast: { error: vi.fn(), info: vi.fn() } }));
const nodes: FlowNode[] = [{ id: 'a', type: 'agentNode', position: { x: 0, y: 0 }, data: { name: 'Assistant', prompt: 'Original' } }];
const props = () => ({ workflowId: 39, initialWorkflowName: 'Assistant', user: { id: 'user' }, initialFlow: { nodes: nodes.map((node) => ({ ...node })), edges: [], viewport: { x: 12, y: 15, zoom: 0.8 } } });

describe('shared editor lifecycle', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        useWorkflowStore.getState().clearStore();
        mocks.defaults.mockResolvedValue({ data: { workflow_configurations: {} } });
        mocks.validate.mockResolvedValue({ data: { is_valid: true, errors: [] } });
    });

    it('retains unsaved edits when a view rerender supplies fresh initial objects', async () => {
        const { result, rerender } = renderHook(useWorkflowState, { initialProps: props() });
        await waitFor(() => expect(result.current.nodes).toHaveLength(1));
        const session = useWorkflowStore.getState().editorSession;
        act(() => { result.current.setNodes([{ ...nodes[0], data: { name: 'Assistant', prompt: 'Unsaved' } }]); result.current.setIsDirty(true); });
        rerender(props());
        expect(result.current.nodes[0].data.prompt).toBe('Unsaved');
        expect(result.current.isDirty).toBe(true);
        expect(useWorkflowStore.getState().editorSession).toBe(session);
    });

    it('saves the draft from Chat without a mounted ReactFlow instance', async () => {
        const { result } = renderHook(useWorkflowState, { initialProps: props() });
        await waitFor(() => expect(result.current.nodes).toHaveLength(1));
        act(() => { result.current.setNodes([{ ...nodes[0], data: { name: 'Assistant', prompt: 'Chat edit' } }]); result.current.setIsDirty(true); });
        mocks.save.mockResolvedValue({ data: { version_number: 2, version_status: 'draft' } });
        expect(result.current.rfInstance.current).toBeNull();
        await act(async () => { await result.current.saveWorkflow(); });
        expect(mocks.save).toHaveBeenCalledWith(expect.objectContaining({ body: expect.objectContaining({ workflow_definition: expect.objectContaining({ nodes: [expect.objectContaining({ data: { name: 'Assistant', prompt: 'Chat edit' } })], viewport: { x: 12, y: 15, zoom: 0.8 } }) }) }));
        expect(result.current.isDirty).toBe(false);
    });

    it('ignores an in-flight save after switching to a version with identical nodes', async () => {
        const { result } = renderHook(useWorkflowState, { initialProps: props() });
        await waitFor(() => expect(result.current.nodes).toHaveLength(1));
        let resolve!: (value: unknown) => void;
        mocks.save.mockReturnValue(new Promise((done) => { resolve = done; }));
        let saving: ReturnType<typeof result.current.saveWorkflow>;
        act(() => { saving = result.current.saveWorkflow(); });
        const versionNodes = result.current.nodes;
        act(() => useWorkflowStore.getState().loadVersionGraph(versionNodes, []));
        let saved;
        await act(async () => { resolve({ data: { workflow_definition: { nodes: [{ ...nodes[0], data: { name: 'Assistant', prompt: 'Normalized old draft' } }], edges: [] }, version_number: 3, version_status: 'draft' } }); saved = await saving; });
        expect(saved).toBeUndefined();
        expect(result.current.nodes).toBe(versionNodes);
    });
});
