/**
 * "To:" -- one bot is its chat, two or more are a group.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const list = vi.hoisted(() => vi.fn());
const createFolder = vi.hoisted(() => vi.fn());
const move = vi.hoisted(() => vi.fn());
const push = vi.hoisted(() => vi.fn());
vi.mock('@/client/sdk.gen', () => ({
    getWorkflowsApiV1WorkflowFetchGet: list,
    createFolderApiV1FolderPost: createFolder,
    moveWorkflowToFolderApiV1WorkflowWorkflowIdFolderPut: move,
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import { groupName, NewChatDialog } from '../NewChatDialog';

beforeEach(() => {
    list.mockReset();
    createFolder.mockReset();
    move.mockReset();
    push.mockReset();
    list.mockResolvedValue({ data: [{ id: 3, name: 'Front desk' }, { id: 4, name: 'Sales agent' }] });
});

describe('the group name', () => {
    it('is the agents, joined', () => {
        expect(groupName([{ id: 3, name: 'Front desk' }, { id: 4, name: 'Sales agent' }])).toBe('Front desk, Sales agent');
    });
});

describe('starting a chat', () => {
    it('one agent opens its chat', async () => {
        render(<NewChatDialog open onOpenChange={() => {}} />);
        fireEvent.click(await screen.findByText('Front desk'));
        fireEvent.click(screen.getByRole('button', { name: 'Open chat' }));
        expect(push).toHaveBeenCalledWith('/workflow/3/thread');
        expect(createFolder).not.toHaveBeenCalled();
    });

    it('two agents make a group: a channel named for them, with both moved in', async () => {
        createFolder.mockResolvedValue({ data: { id: 9, name: 'Front desk, Sales agent' } });
        move.mockResolvedValue({ data: {} });
        render(<NewChatDialog open onOpenChange={() => {}} />);
        fireEvent.click(await screen.findByText('Front desk'));
        fireEvent.click(screen.getByText('Sales agent'));
        fireEvent.click(screen.getByRole('button', { name: /Create group chat/ }));
        await waitFor(() => expect(createFolder).toHaveBeenCalledWith({ body: { name: 'Front desk, Sales agent' } }));
        await waitFor(() => expect(move).toHaveBeenCalledTimes(2));
        expect(move.mock.calls.map((c) => c[0].path.workflow_id).sort()).toEqual([3, 4]);
        expect(move.mock.calls[0][0].body).toEqual({ folder_id: 9 });
        await waitFor(() => expect(push).toHaveBeenCalledWith('/channels/9'));
    });

    it('searches the agents', async () => {
        render(<NewChatDialog open onOpenChange={() => {}} />);
        await screen.findByText('Front desk');
        fireEvent.change(screen.getByLabelText('To'), { target: { value: 'sales' } });
        expect(screen.queryByText('Front desk')).toBeNull();
        expect(screen.getByText('Sales agent')).toBeTruthy();
    });
});
