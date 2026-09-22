import { ReactFlowInstance } from '@xyflow/react';
import { EdgeChange,NodeChange } from '@xyflow/system';
import { create } from 'zustand';

import { WorkflowError } from '@/client/types.gen';
import { FlowEdge, FlowNode, NodeType } from '@/components/flow/types';
import { WorkflowConfigurations } from '@/types/workflow-configurations';

interface HistoryState {
  nodes: FlowNode[];
  edges: FlowEdge[];
  workflowName: string;
}

interface WorkflowState {
  // Workflow identification
  workflowId: number | null;
  editorSession: number;
  workflowName: string;

  // Flow state
  nodes: FlowNode[];
  edges: FlowEdge[];

  // History for undo/redo
  history: HistoryState[];
  historyIndex: number;
  dragStart: HistoryState | null;

  // UI state (not tracked in history)
  isDirty: boolean;
  isAddNodePanelOpen: boolean;

  // Validation state
  workflowValidationErrors: WorkflowError[];

  // Configuration
  templateContextVariables: Record<string, string>;
  workflowConfigurations: WorkflowConfigurations | null;
  dictionary: string;

  // ReactFlow instance reference
  rfInstance: ReactFlowInstance<FlowNode, FlowEdge> | null;
}

interface WorkflowActions {
  deleteGraphElements: (nodeIds: string[], edgeIds: string[]) => void;
  applyGraphProposal: (workflowId: number, base: string, nodes: FlowNode[], edges: FlowEdge[]) => boolean;
  acceptSavedGraph: (workflowId: number, editorSession: number, base: string, name: string, nodes?: FlowNode[], edges?: FlowEdge[]) => boolean;
  loadVersionGraph: (nodes: FlowNode[], edges: FlowEdge[]) => void;
  // Initialization
  initializeWorkflow: (
    workflowId: number,
    workflowName: string,
    nodes: FlowNode[],
    edges: FlowEdge[],
    templateContextVariables?: Record<string, string>,
    workflowConfigurations?: WorkflowConfigurations | null,
    dictionary?: string
  ) => void;

  // History management
  pushToHistory: () => void;
  undo: () => void;
  redo: () => void;
  canUndo: () => boolean;
  canRedo: () => boolean;

  // Node operations
  setNodes: (nodes: FlowNode[], changes?: NodeChange<FlowNode>[]) => void;
  addNode: (node: FlowNode) => void;
  updateNode: (nodeId: string, updates: Partial<FlowNode>) => void;
  deleteNode: (nodeId: string) => void;

  // Edge operations
  setEdges: (edges: FlowEdge[], changes?: EdgeChange<FlowEdge>[]) => void;
  addEdge: (edge: FlowEdge) => void;
  updateEdge: (edgeId: string, updates: Partial<FlowEdge>) => void;
  deleteEdge: (edgeId: string) => void;

  // Workflow metadata
  setWorkflowName: (name: string) => void;
  setTemplateContextVariables: (variables: Record<string, string>) => void;
  setWorkflowConfigurations: (configurations: WorkflowConfigurations) => void;
  setDictionary: (dictionary: string) => void;

  // UI state
  setIsDirty: (isDirty: boolean) => void;
  setIsAddNodePanelOpen: (isOpen: boolean) => void;

  // Validation
  setWorkflowValidationErrors: (errors: WorkflowError[]) => void;
  markNodeAsInvalid: (nodeId: string, message: string) => void;
  markEdgeAsInvalid: (edgeId: string, message: string) => void;
  clearValidationErrors: () => void;

  // ReactFlow instance
  setRfInstance: (instance: ReactFlowInstance<FlowNode, FlowEdge> | null) => void;

  // Clear store (for cleanup)
  clearStore: () => void;
}

type WorkflowStore = WorkflowState & WorkflowActions;

const MAX_HISTORY_SIZE = 50;

// Selection, measurements and validation paint are not edits to an agent.
export function graphSnapshot(nodes: FlowNode[], edges: FlowEdge[]): string {
  const clean = (item: FlowNode | FlowEdge) => {
    const copy = { ...item } as Record<string, unknown>;
    for (const key of ['selected', 'dragging', 'measured', 'width', 'height', 'resizing']) delete copy[key];
    if (item.data) {
      const data = { ...item.data } as Record<string, unknown>;
      delete data.invalid;
      delete data.validationMessage;
      delete data.runtime_active;
      delete data.selected_through_edge;
      delete data.hovered_through_edge;
      copy.data = data;
    }
    return copy;
  };
  return JSON.stringify({ nodes: nodes.map(clean), edges: edges.map(clean) });
}

function sameHistory(a: HistoryState | undefined, b: HistoryState): boolean {
  return !!a && a.workflowName === b.workflowName && graphSnapshot(a.nodes, a.edges) === graphSnapshot(b.nodes, b.edges);
}

/** Commit both endpoints once, retaining the original position across drag frames. */
function commitGraph(state: WorkflowState, updates: Partial<HistoryState>): Partial<WorkflowState> {
  const before = state.dragStart ?? { nodes: state.nodes, edges: state.edges, workflowName: state.workflowName };
  const after = { nodes: state.nodes, edges: state.edges, workflowName: state.workflowName, ...updates };
  if (sameHistory(before, after)) return { ...after, dragStart: null };
  const history = state.history.slice(0, state.historyIndex + 1);
  if (!sameHistory(history.at(-1), before)) history.push(before);
  history.push(after);
  const bounded = history.slice(-MAX_HISTORY_SIZE);
  return { ...after, history: bounded, historyIndex: bounded.length - 1, dragStart: null, isDirty: true };
}

// Create the store
export const useWorkflowStore = create<WorkflowStore>((set, get) => ({
  // Initial state
  workflowId: null,
  editorSession: 0,
  workflowName: '',
  nodes: [],
  edges: [],
  history: [],
  historyIndex: -1,
  dragStart: null,
  isDirty: false,
  isAddNodePanelOpen: false,
  workflowValidationErrors: [],
  templateContextVariables: {},
  workflowConfigurations: null,
  dictionary: '',
  rfInstance: null,

  // Actions
  deleteGraphElements: (nodeIds, edgeIds) => {
    const state = get();
    // A conversation needs somewhere to begin, so the start step is not
    // deletable. Its toolbar has always hidden Delete for that reason; the
    // canvas keyboard reached past the toolbar, and Backspace on a selected
    // start step left a draft with no way in. Only the steps that actually
    // go take their connections with them, so refusing one here does not
    // strand the edges attached to it.
    const removedNodes = new Set(
      nodeIds.filter(id => state.nodes.find(node => node.id === id)?.type !== NodeType.START_CALL),
    );
    const removedEdges = new Set(edgeIds);
    set(commitGraph(state, {
      nodes: state.nodes.filter(node => !removedNodes.has(node.id)),
      edges: state.edges.filter(edge => !removedEdges.has(edge.id) && !removedNodes.has(edge.source) && !removedNodes.has(edge.target)),
    }));
  },
  acceptSavedGraph: (workflowId, editorSession, base, name, nodes, edges) => {
    const state = get();
    if (state.editorSession !== editorSession || state.workflowId !== workflowId || state.workflowName !== name || graphSnapshot(state.nodes, state.edges) !== base) return false;
    const normalized: HistoryState = { nodes: nodes ?? state.nodes, edges: edges ?? state.edges, workflowName: name };
    // Server defaults belong to the saved state, not a separate user edit.
    // Keep redo of that state and the next edit's baseline normalized as well.
    const history = state.history.slice();
    if (state.historyIndex >= 0) history[state.historyIndex] = normalized;
    set({ ...normalized, history, isDirty: false });
    return true;
  },
  applyGraphProposal: (workflowId, base, nodes, edges) => {
    const state = get();
    if (state.workflowId !== workflowId || graphSnapshot(state.nodes, state.edges) !== base) return false;
    set({ ...commitGraph(state, { nodes, edges }), workflowValidationErrors: [] });
    return true;
  },
  loadVersionGraph: (nodes, edges) => {
    const state = get();
    set({ nodes, edges, editorSession: state.editorSession + 1,
      history: [{ nodes, edges, workflowName: state.workflowName }], historyIndex: 0, dragStart: null,
      isDirty: false, workflowValidationErrors: [], isAddNodePanelOpen: false });
  },
  initializeWorkflow: (workflowId, workflowName, nodes, edges, templateContextVariables = {}, workflowConfigurations = null, dictionary = '') => {
    const initialHistory: HistoryState = { nodes, edges, workflowName };
    set({
      workflowId,
      editorSession: get().editorSession + 1,
      workflowName,
      nodes,
      edges,
      templateContextVariables,
      workflowConfigurations,
      dictionary,
      isDirty: false,
      workflowValidationErrors: [],
      history: [initialHistory],
      historyIndex: 0,
      dragStart: null,
    });
  },

  pushToHistory: () => {
    const state = get();
    const currentState: HistoryState = {
      nodes: state.nodes,
      edges: state.edges,
      workflowName: state.workflowName,
    };

    if (sameHistory(state.history[state.historyIndex], currentState)) return;
    const history = [...state.history.slice(0, state.historyIndex + 1), currentState].slice(-MAX_HISTORY_SIZE);
    set({ history, historyIndex: history.length - 1 });
  },

  undo: () => {
    const state = get();
    if (state.historyIndex > 0) {
      const newIndex = state.historyIndex - 1;
      const historicState = state.history[newIndex];
      set({
        nodes: historicState.nodes,
        edges: historicState.edges,
        workflowName: historicState.workflowName,
        historyIndex: newIndex,
        dragStart: null,
        isDirty: true,
      });
    }
  },

  redo: () => {
    const state = get();
    if (state.historyIndex < state.history.length - 1) {
      const newIndex = state.historyIndex + 1;
      const historicState = state.history[newIndex];
      set({
        nodes: historicState.nodes,
        edges: historicState.edges,
        workflowName: historicState.workflowName,
        historyIndex: newIndex,
        dragStart: null,
        isDirty: true,
      });
    }
  },

  canUndo: () => {
    const state = get();
    return state.historyIndex > 0;
  },

  canRedo: () => {
    const state = get();
    return state.historyIndex < state.history.length - 1;
  },

  setNodes: (nodes, changes) => {
    const state = get();
    const dragging = changes?.some(change => change.type === 'position' && change.dragging === true);
    const committed = changes?.some(change => change.type === 'add' || change.type === 'remove' || change.type === 'replace' || (change.type === 'position' && change.dragging !== true));
    if (committed) {
      // React Flow reports node and attached-edge deletions separately. Record
      // a complete graph now so one Undo cannot restore dangling connections.
      const removedIds = new Set(changes?.filter(change => change.type === 'remove').map(change => change.id));
      const edges = removedIds.size
        ? state.edges.filter(edge => !removedIds.has(edge.source) && !removedIds.has(edge.target))
        : state.edges;
      set(commitGraph(state, { nodes, edges }));
    } else if (dragging) {
      set({ nodes, isDirty: true, dragStart: state.dragStart ?? { nodes: state.nodes, edges: state.edges, workflowName: state.workflowName } });
    } else {
      // Selection, dimensions and initialization do not create undo entries.
      set({ nodes });
    }
  },

  addNode: (node) => {
    const state = get();
    set(commitGraph(state, { nodes: [...state.nodes, node] }));
  },
  updateNode: (nodeId, updates) => {
    const state = get();
    set(commitGraph(state, { nodes: state.nodes.map(node => node.id === nodeId ? { ...node, ...updates } : node) }));
  },
  deleteNode: (nodeId) => {
    const state = get();
    if (state.nodes.find(node => node.id === nodeId)?.type === NodeType.START_CALL) return;
    set(commitGraph(state, {
      nodes: state.nodes.filter(node => node.id !== nodeId),
      edges: state.edges.filter(edge => edge.source !== nodeId && edge.target !== nodeId),
    }));
  },
  setEdges: (edges, changes) => {
    const state = get();
    if (changes?.some(change => change.type === 'add' || change.type === 'remove' || change.type === 'replace')) {
      set(commitGraph(state, { edges }));
    } else {
      set({ edges });
    }
  },
  addEdge: (edge) => {
    const state = get();
    set(commitGraph(state, { edges: [...state.edges, edge] }));
  },
  updateEdge: (edgeId, updates) => {
    const state = get();
    set(commitGraph(state, { edges: state.edges.map(edge => edge.id === edgeId ? { ...edge, ...updates } : edge) }));
  },
  deleteEdge: (edgeId) => {
    const state = get();
    set(commitGraph(state, { edges: state.edges.filter(edge => edge.id !== edgeId) }));
  },
  setWorkflowName: (workflowName) => {
    set(commitGraph(get(), { workflowName }));
  },

  setTemplateContextVariables: (templateContextVariables) => {
    set({ templateContextVariables });
  },

  setWorkflowConfigurations: (workflowConfigurations) => {
    set({ workflowConfigurations });
  },

  setDictionary: (dictionary) => {
    set({ dictionary });
  },

  setIsDirty: (isDirty) => {
    set({ isDirty });
  },

  setIsAddNodePanelOpen: (isAddNodePanelOpen) => {
    set({ isAddNodePanelOpen });
  },

  setWorkflowValidationErrors: (workflowValidationErrors) => {
    set({ workflowValidationErrors });
  },

  markNodeAsInvalid: (nodeId, message) => {
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.id === nodeId
          ? { ...node, data: { ...node.data, invalid: true, validationMessage: message } }
          : node
      ),
    }));
  },

  markEdgeAsInvalid: (edgeId, message) => {
    set((state) => ({
      edges: state.edges.map((edge) =>
        edge.id === edgeId
          ? { ...edge, data: { ...edge.data, invalid: true, validationMessage: message } }
          : edge
      ),
    }));
  },

  clearValidationErrors: () => {
    set((state) => ({
      nodes: state.nodes.map((node) => ({
        ...node,
        data: { ...node.data, invalid: false, validationMessage: null },
      })),
      edges: state.edges.map((edge) => ({
        ...edge,
        data: { ...edge.data, invalid: false, validationMessage: null },
      })),
      workflowValidationErrors: [],
    }));
  },

  setRfInstance: (rfInstance) => {
    set({ rfInstance });
  },

  clearStore: () => {
    set({
      workflowId: null,
      editorSession: get().editorSession + 1,
      workflowName: '',
      nodes: [],
      edges: [],
      history: [],
      historyIndex: -1,
  dragStart: null,
      isDirty: false,
      isAddNodePanelOpen: false,
      workflowValidationErrors: [],
      templateContextVariables: {},
      workflowConfigurations: null,
      dictionary: '',
      rfInstance: null,
    });
  },
}));

// Selectors for common use cases
export const useWorkflowNodes = () => useWorkflowStore((state) => state.nodes);
export const useWorkflowEdges = () => useWorkflowStore((state) => state.edges);
export const useWorkflowName = () => useWorkflowStore((state) => state.workflowName);
export const useWorkflowId = () => useWorkflowStore((state) => state.workflowId);
export const useWorkflowDirtyState = () => useWorkflowStore((state) => state.isDirty);
export const useWorkflowValidationErrors = () => useWorkflowStore((state) => state.workflowValidationErrors);

// Selector for undo/redo state
export const useUndoRedo = () => {
  const undo = useWorkflowStore((state) => state.undo);
  const redo = useWorkflowStore((state) => state.redo);
  const canUndo = useWorkflowStore((state) => state.canUndo());
  const canRedo = useWorkflowStore((state) => state.canRedo());

  return { undo, redo, canUndo, canRedo };
};
