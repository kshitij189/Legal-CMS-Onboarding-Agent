// Minimal typed client for the backend API. The bearer token lives in sessionStorage: it is
// scoped to this browser tab and cleared on sign-out (local demo application).

const TOKEN_KEY = "onboarding.token";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown>[] = [],
  ) {
    super(message);
  }
}

export function getToken(): string | null {
  return typeof window === "undefined" ? null : window.sessionStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
  else window.sessionStorage.removeItem(TOKEN_KEY);
}

export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown; headers?: Record<string, string> } = {},
): Promise<T> {
  const token = getToken();
  const response = await fetch(`/api/v1${path}`, {
    method: options.method ?? "GET",
    headers: {
      ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
  if (response.status === 204) return undefined as T;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = payload?.error ?? {};
    if (response.status === 401) setToken(null);
    throw new ApiError(
      response.status,
      error.code ?? `http_${response.status}`,
      error.message ?? `Request failed (${response.status}).`,
      error.details ?? [],
    );
  }
  return payload as T;
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const details = error.details
      .map((d) => (typeof d.message === "string" ? d.message : typeof d.code === "string" ? d.code : ""))
      .filter(Boolean);
    return details.length ? `${error.message} (${details.join("; ")})` : error.message;
  }
  return error instanceof Error ? error.message : String(error);
}
