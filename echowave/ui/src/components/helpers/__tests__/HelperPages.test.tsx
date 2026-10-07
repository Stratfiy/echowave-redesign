/**
 * The pages the helpers open: a saved report that matches its export, who
 * owes me with each follow-up's real delivery state, "Save as report" under
 * a reply, and /start sending nobody through the old journey while the
 * builder is on.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { builderPath, StartGate } from '../StartGate';

const sdk = vi.hoisted(() => ({
    getReport: vi.fn(),
    exportReport: vi.fn(),
    share: vi.fn(),
    owed: vi.fn(),
    fromReply: vi.fn(),
}));
vi.mock('@/client/sdk.gen', () => ({
    getReportApiV1HelpersReportsReportUuidGet: sdk.getReport,
    exportReportApiV1HelpersReportsReportUuidExportGet: sdk.exportReport,
    shareReportApiV1HelpersReportsReportUuidVisibilityPut: sdk.share,
    listReportsApiV1HelpersReportsGet: vi.fn(),
    whoOwesMeApiV1HelpersWhoOwesMeGet: sdk.owed,
    addCommitmentApiV1HelpersCommitmentsPost: vi.fn(),
    changeCommitmentApiV1HelpersCommitmentsCommitmentUuidPatch: vi.fn(),
    saveReplyAsReportApiV1HelpersReportsFromReplyPost: sdk.fromReply,
}));
const auth = vi.hoisted(() => ({ user: { id: 1 }, loading: false }));
vi.mock('@/lib/auth', () => ({ useAuth: () => auth }));
const state = vi.hoisted(() => ({ feature: 'off' as 'loading' | 'on' | 'off' }));
vi.mock('@/lib/helpers', async (original) => ({
    ...(await original<typeof import('@/lib/helpers')>()),
    useFeatureState: () => state.feature,
    downloadText: vi.fn(),
}));
const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));

const REPORT = {
    uuid: 'r-1',
    kind: 'research',
    title: 'Home bakery GST',
    question: 'GST for a home bakery?',
    summary: 'Registration depends on turnover.',
    findings: [
        { statement: 'The threshold is 40 lakh.', basis: 'source', sources: [1], as_of: '2026-04-01' },
        { statement: 'Most home bakers will not register.', basis: 'inference', sources: [] },
    ],
    conflicts: [{ statement: 'One blog says 20 lakh.', sources: [2] }],
    inaccessible: [{ url: 'https://gst.gov.in/x', reason: 'Timed out' }],
    sources: [
        { n: 1, url: 'https://cbic.gov.in/gst', title: 'CBIC' },
        { n: 2, url: 'https://blog.example/gst', title: 'A blog' },
    ],
    body: '# Home bakery GST\n',
    content_hash: 'abcdef1234567890',
    visibility: 'private',
    mine: true,
    thread_id: 't-1',
    created_at: '2026-10-07T10:00:00Z',
    notice: null,
};

beforeEach(() => {
    Object.values(sdk).forEach((fn) => fn.mockReset());
    router.replace.mockReset();
});

describe('a saved report', () => {
    it('keeps source, inference, disagreement and unreadable sources apart', async () => {
        sdk.getReport.mockResolvedValue({ data: REPORT });
        const { ReportView } = await import('../ReportView');
        render(<ReportView uuid="r-1" />);
        expect(await screen.findByText('The threshold is 40 lakh.')).toBeTruthy();
        expect(screen.getByText('Source')).toBeTruthy();
        expect(screen.getByText('Inference')).toBeTruthy();
        expect(screen.getByText('One blog says 20 lakh.')).toBeTruthy();
        expect(screen.getByText(/Timed out/)).toBeTruthy();
        expect(screen.getByText('abcdef12')).toBeTruthy();
        expect(screen.getByText('Private to you', { exact: false })).toBeTruthy();
    });

    it('exports the server rendering, under the version shown', async () => {
        sdk.getReport.mockResolvedValue({ data: REPORT });
        sdk.exportReport.mockResolvedValue({ data: '# Home bakery GST\n' });
        const helpers = await import('@/lib/helpers');
        const { ReportView } = await import('../ReportView');
        render(<ReportView uuid="r-1" />);
        fireEvent.click(await screen.findByRole('button', { name: /Export Markdown/ }));
        await waitFor(() => expect(helpers.downloadText).toHaveBeenCalled());
        expect(sdk.exportReport.mock.calls[0][0]).toMatchObject({ path: { report_uuid: 'r-1' }, query: { format: 'md' } });
        expect(helpers.downloadText).toHaveBeenCalledWith('# Home bakery GST\n', 'Home-bakery-GST-abcdef12.md', 'text/markdown');
    });

    it('a report that does not load says so with Retry', async () => {
        sdk.getReport.mockResolvedValue({ error: { detail: 'Report not found' } });
        const { ReportView } = await import('../ReportView');
        render(<ReportView uuid="nope" />);
        expect(await screen.findByText('The report did not load')).toBeTruthy();
        expect(screen.getByText('Report not found')).toBeTruthy();
    });
});

describe('who owes me', () => {
    it('totals what is owed and says each follow-up as its card says it', async () => {
        sdk.owed.mockResolvedValue({
            data: {
                items: [
                    {
                        uuid: 'c-1',
                        id: 1,
                        direction: 'owed_to_me',
                        counterparty: 'Ravi',
                        description: 'invoice 12',
                        amount_minor: 480000,
                        currency: 'INR',
                        amount: '₹4,800',
                        due_on: '2026-10-01',
                        overdue: true,
                        status: 'open',
                        visibility: 'private',
                        mine: true,
                        revision: 1,
                        follow_up: { card_id: 9, delivery: 'outcome_unknown' },
                    },
                ],
                totals: [{ currency: 'INR', amount_minor: 480000, amount: '₹4,800' }],
                overdue: 1,
            },
        });
        const { WhoOwesMe } = await import('../WhoOwesMe');
        render(<WhoOwesMe />);
        expect(await screen.findByText('₹4,800 owed by 1 person, 1 overdue.')).toBeTruthy();
        expect(screen.getByText(/Overdue since 2026-10-01/)).toBeTruthy();
        expect(screen.getByTestId('follow-up-state').textContent).toContain('Please do not send it again');
    });

    it('nothing tracked is said as nothing tracked', async () => {
        sdk.owed.mockResolvedValue({ data: { items: [], totals: [], overdue: 0 } });
        const { WhoOwesMe } = await import('../WhoOwesMe');
        render(<WhoOwesMe />);
        expect(await screen.findByText('Nobody owes you anything tracked here.')).toBeTruthy();
    });
});

describe('save as report', () => {
    it('saves the reply and links to it; a failure is said, never saved', async () => {
        const { SaveReportButton } = await import('../SaveReportButton');
        sdk.fromReply.mockResolvedValueOnce({ error: { detail: 'Reply not found' } });
        sdk.fromReply.mockResolvedValueOnce({ data: { uuid: 'r-9' } });
        render(<SaveReportButton eventId={42} />);
        fireEvent.click(screen.getByTestId('save-report'));
        expect(await screen.findByText('Reply not found')).toBeTruthy();
        fireEvent.click(screen.getByTestId('save-report'));
        const link = await screen.findByTestId('saved-report-link');
        expect(link.getAttribute('href')).toBe('/saved-reports/r-9');
        expect(sdk.fromReply.mock.calls[0][0]).toEqual({ body: { event_id: 42 } });
    });
});

describe('/start', () => {
    it('opens the builder in Chat while the builder is on', async () => {
        state.feature = 'on';
        render(
            <StartGate>
                <p>old journey</p>
            </StartGate>,
        );
        expect(screen.queryByText('old journey')).toBeNull();
        await waitFor(() => expect(router.replace).toHaveBeenCalledWith('/overview?helper=builder'));
    });

    it('is the old journey, unchanged, while the builder is off', () => {
        state.feature = 'off';
        render(
            <StartGate>
                <p>old journey</p>
            </StartGate>,
        );
        expect(screen.getByText('old journey')).toBeTruthy();
        expect(router.replace).not.toHaveBeenCalled();
    });

    it('turns a template link into words for the box', () => {
        expect(builderPath('?template=clinic_appointment')).toBe(
            '/overview?helper=builder&say=Set+up+the+clinic+appointment+agent+for+me',
        );
    });
});
