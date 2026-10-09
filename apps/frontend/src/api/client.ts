import type { StreamEvent } from './types';

class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData bodies must NOT get a JSON content-type — the browser sets the
  // multipart boundary itself. Only JSON string bodies get the header.
  const isForm = typeof FormData !== 'undefined' && init?.body instanceof FormData;
  const res = await fetch(path, {
    credentials: 'include',
    headers: init?.body && !isForm ? { 'content-type': 'application/json' } : undefined,
    ...init,
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { error?: { code?: string; message?: string } } | null;
    // Proxy-level failures come back as HTML, not our JSON envelope - say what they mean.
    const proxyMessage: Record<number, string> = {
      413: 'That file is larger than the server accepts.',
      502: 'The server is restarting or unavailable - try again in a moment.',
      504: 'The server took too long to respond - try again, or use a smaller file.',
    };
    throw new ApiError(
      body?.error?.code ?? 'HTTP_ERROR',
      body?.error?.message ?? proxyMessage[res.status] ?? `Request failed (${res.status})`,
      res.status,
    );
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body === undefined ? undefined : JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  /** multipart POST for file uploads (attachments, codebase). */
  upload: <T>(path: string, file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<T>(path, { method: 'POST', body: fd });
  },
};

/** Shared SSE reader — invokes onEvent per StreamEvent frame. POST by default; pass
 *  method 'GET' for reconnect/progress streams (no body). */
async function streamSse<E = StreamEvent>(
  url: string, body: unknown, onEvent: (e: E) => void, signal?: AbortSignal,
  method: 'GET' | 'POST' = 'POST',
): Promise<void> {
  const sendBody = method === 'POST' && body !== undefined;
  const res = await fetch(url, {
    method, credentials: 'include',
    headers: sendBody ? { 'content-type': 'application/json' } : undefined,
    body: sendBody ? JSON.stringify(body) : undefined,
    signal,
  });
  if (!res.ok || !res.body) {
    const errBody = (await res.json().catch(() => null)) as { error?: { code?: string; message?: string } } | null;
    onEvent({ type: 'error', code: errBody?.error?.code ?? 'HTTP_ERROR', message: errBody?.error?.message ?? `Request failed (${res.status})` } as E);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';
    for (const frame of frames) {
      const data = frame.split('\n').filter((l) => l.startsWith('data: ')).map((l) => l.slice(6)).join('');
      if (!data) continue;
      try { onEvent(JSON.parse(data) as E); } catch { /* ignore malformed frame */ }
    }
  }
}

/** POST a stage's reviewed plan trigger (SSE); invokes onEvent per StreamEvent (D-56). */
export function streamStageTrigger(
  projectId: string, phase: number, onEvent: (e: StreamEvent) => void, signal?: AbortSignal,
): Promise<void> {
  return streamSse(`/api/projects/${projectId}/phase/${phase}/plan/trigger`, undefined, onEvent, signal);
}

/** Reconnect to a running stage's live progress (SSE, GET) — replays buffered
 *  events then live-tails. Used to resume the "Generating…" view after navigating
 *  away/refresh (D-97 L2). */
export function streamStageProgress(
  projectId: string, phase: number, onEvent: (e: StreamEvent) => void, signal?: AbortSignal,
): Promise<void> {
  return streamSse(`/api/projects/${projectId}/phase/${phase}/stream`, undefined, onEvent, signal, 'GET');
}

/** POST /api/chat with SSE response; invokes onEvent per StreamEvent. */
export function streamChat(
  body: { projectId?: string; message: string; referencedArtifactIds?: string[]; attachmentIds?: string[]; formworkIds?: string[] },
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamSse('/api/chat', body, onEvent, signal);
}

/** POST a code-assistant request (SSE): its steps, proposed changes and summary arrive as events. */
export function streamCodeEdit<E>(
  projectId: string, body: { scope: string; prompt: string; targets: string[]; sessionId?: string | null },
  onEvent: (e: E) => void, signal?: AbortSignal,
): Promise<void> {
  return streamSse<E>(`/api/projects/${projectId}/code-edit`, body, onEvent, signal);
}
