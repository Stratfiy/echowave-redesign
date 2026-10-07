/**
 * The generated client as the app uses it: the base URL from config, the
 * bearer token attached by the interceptor at request time, and errors
 * turned into ApiError with FastAPI's detail -- never silently "data".
 */
import { healthApiV1HealthGet, threadsApiV1TimelineThreadsGet, postMessageApiV1TimelineMessagePost } from '@/client/sdk.gen';
import { ApiError, call, client, detailFromError } from '@/lib/api';
import { installAuth, setToken, setUnauthorizedListener } from '@/lib/auth/token';

type Seen = { url: string; method: string; auth: string | null; body: string | null };

function respond(status: number, body: unknown) {
    const seen: Seen[] = [];
    const fetchMock = jest.fn(async (input: Request) => {
        seen.push({
            url: input.url,
            method: input.method,
            auth: input.headers.get('Authorization'),
            body: input.method === 'GET' ? null : await input.clone().text(),
        });
        return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    });
    client.setConfig({ baseUrl: 'https://api.test', fetch: fetchMock as unknown as typeof fetch });
    return seen;
}

beforeAll(() => installAuth());
afterEach(() => {
    setToken(null);
    setUnauthorizedListener(null);
});

test('requests go to the configured API with the bearer token read at request time', async () => {
    const seen = respond(200, { threads: [] });
    setToken('first');
    await call(threadsApiV1TimelineThreadsGet({ query: { limit: 50 } }));
    setToken('second');
    await call(threadsApiV1TimelineThreadsGet({ query: { limit: 50 } }));
    expect(seen.map((s) => s.url)).toEqual([
        'https://api.test/api/v1/timeline/threads?limit=50',
        'https://api.test/api/v1/timeline/threads?limit=50',
    ]);
    expect(seen.map((s) => s.auth)).toEqual(['Bearer first', 'Bearer second']);
});

test('signed out, no Authorization header is sent', async () => {
    const seen = respond(200, { status: 'ok', features: {} });
    await call(healthApiV1HealthGet());
    expect(seen[0].auth).toBeNull();
});

test('the message body is the generated shape, sent as JSON', async () => {
    const seen = respond(200, { asked: [], unknown: [], ambiguous: [] });
    setToken('t');
    await call(postMessageApiV1TimelineMessagePost({ body: { assistant: true, text: 'Hi', thread_id: 'abc' } }));
    expect(seen[0].method).toBe('POST');
    expect(JSON.parse(seen[0].body as string)).toEqual({ assistant: true, text: 'Hi', thread_id: 'abc' });
});

test('a 409 becomes an ApiError carrying the detail, never data', async () => {
    respond(409, { detail: { message: 'This changed since you opened it.', stored: { revision: 3 } } });
    setToken('t');
    const error = await call(threadsApiV1TimelineThreadsGet()).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.message).toBe('This changed since you opened it.');
    expect((error.detail as { stored: { revision: number } }).stored.revision).toBe(3);
});

test('a 422 array detail reads as words with the field named', () => {
    expect(
        detailFromError({ detail: [{ loc: ['body', 'text'], msg: 'Field required' }] }),
    ).toBe('text: Field required');
});

test('a 401 on a signed-in request signs the person out', async () => {
    respond(401, { detail: 'Invalid or expired token' });
    const listener = jest.fn();
    setUnauthorizedListener(listener);
    setToken('expired');
    await call(threadsApiV1TimelineThreadsGet()).catch(() => undefined);
    expect(listener).toHaveBeenCalledTimes(1);
});
