import { beforeEach, describe, expect, it } from 'vitest';

import { FlowEdge, FlowNode } from '@/components/flow/types';

import { graphSnapshot, useWorkflowStore } from '../workflowStore';

const nodes: FlowNode[] = [{ id: 'a', type: 'agentNode', position: { x: 20, y: 40 }, data: { name: 'Assistant', prompt: 'Original', tool_uuids: ['calendar'] } }];
const edges: FlowEdge[] = [];
const edited = (prompt: string): FlowNode[] => [{ ...nodes[0], data: { ...nodes[0].data, prompt } }];
const store = () => useWorkflowStore.getState();

beforeEach(() => store().initializeWorkflow(39, 'Assistant', nodes, edges));

describe('shared editor draft', () => {
  it('rejects an earlier save even when the newly loaded version has identical content', () => {
    const session = store().editorSession;
    const base = graphSnapshot(nodes, edges);
    store().loadVersionGraph(nodes, edges);
    expect(store().acceptSavedGraph(39, session, base, 'Assistant', edited('Server normalization'), edges)).toBe(false);
    expect(store().nodes).toBe(nodes);
    store().initializeWorkflow(39, 'Assistant', nodes, edges);
    expect(store().acceptSavedGraph(39, session, base, 'Assistant', edited('Stale'), edges)).toBe(false);
  });

  it('resets undo history atomically when loading another version', () => {
    store().applyGraphProposal(39, graphSnapshot(nodes, edges), edited('Draft edits'), edges);
    expect(store().canUndo()).toBe(true);
    store().loadVersionGraph(edited('Historical version'), edges);
    expect(store().canUndo()).toBe(false);
    expect(store().canRedo()).toBe(false);
    store().undo();
    expect(store().nodes[0].data.prompt).toBe('Historical version');
    expect(store().isDirty).toBe(false);
  });
  it('applies a proposal with one-step undo and redo, retaining voice configuration', () => {
    const voice = { stt: { provider: 'deepgram' }, tts: { provider: 'elevenlabs' } };
    useWorkflowStore.setState({ workflowConfigurations: voice as never });
    store().setNodes(edited('Unsaved manual edit'));
    const base = graphSnapshot(store().nodes, store().edges);
    expect(store().applyGraphProposal(39, base, edited('Proposed'), edges)).toBe(true);
    expect(store().isDirty).toBe(true);
    expect(store().workflowConfigurations).toBe(voice);
    expect(store().nodes[0].data.tool_uuids).toEqual(['calendar']);
    store().undo();
    expect(store().nodes[0].data.prompt).toBe('Unsaved manual edit');
    store().redo();
    expect(store().nodes[0].data.prompt).toBe('Proposed');
  });

  it('rejects a proposal after graph edits or switching agents', () => {
    const base = graphSnapshot(nodes, edges);
    store().setNodes(edited('Newer work'));
    expect(store().applyGraphProposal(39, base, edited('Stale'), edges)).toBe(false);
    expect(store().nodes[0].data.prompt).toBe('Newer work');
    store().initializeWorkflow(40, 'Same name', nodes, edges);
    expect(store().applyGraphProposal(39, base, edited('Wrong agent'), edges)).toBe(false);
    expect(store().nodes).toBe(nodes);
  });

  it('ignores selection, measurements, and validation paint', () => {
    const painted = [{ ...nodes[0], selected: true, measured: { width: 280, height: 100 }, data: { ...nodes[0].data, invalid: true, validationMessage: 'Check me' } }];
    expect(graphSnapshot(painted, edges)).toBe(graphSnapshot(nodes, edges));
  });

  it('does not overwrite edits made while Save was in flight', () => {
    const base = graphSnapshot(nodes, edges);
    store().setNodes(edited('Newer edit'));
    store().setIsDirty(true);
    expect(store().acceptSavedGraph(39, store().editorSession, base, 'Assistant', edited('Server draft'), edges)).toBe(false);
    expect(store().nodes[0].data.prompt).toBe('Newer edit');
    expect(store().isDirty).toBe(true);
  });

  it('accepts server normalization only for the saved draft', () => {
    store().setIsDirty(true);
    expect(store().acceptSavedGraph(39, store().editorSession, graphSnapshot(nodes, edges), 'Assistant', edited('Normalized'), edges)).toBe(true);
    expect(store().nodes[0].data.prompt).toBe('Normalized');
    expect(store().isDirty).toBe(false);
  });

  it('rejects save responses after switching agents or renaming', () => {
    const base = graphSnapshot(nodes, edges);
    store().setWorkflowName('Renamed');
    expect(store().acceptSavedGraph(39, store().editorSession, base, 'Assistant', nodes, edges)).toBe(false);
    store().initializeWorkflow(40, 'Assistant', nodes, edges);
    expect(store().acceptSavedGraph(39, store().editorSession, base, 'Assistant', nodes, edges)).toBe(false);
  });
});
