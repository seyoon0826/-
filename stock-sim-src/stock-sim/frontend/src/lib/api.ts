// 백엔드 API 클라이언트 (SPEC 10). 응답 envelope {data, meta} / 에러 {error: {code, message}}.
export const API_ORIGIN = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export const API_BASE = `${API_ORIGIN}/api/v1`;
export const WS_BASE = `${API_ORIGIN.replace(/^http/, "ws")}/api/v1`;

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

export interface Envelope<T> {
  data: T;
  meta: Record<string, unknown>;
}

export async function apiRequest<T>(
  path: string,
  opts: { method?: string; token?: string | null; body?: unknown; headers?: Record<string, string> } = {},
): Promise<Envelope<T>> {
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "서버에 연결할 수 없습니다.");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(res.status, body?.error?.code ?? "HTTP_ERROR", body?.error?.message ?? res.statusText);
  }
  return body as Envelope<T>;
}

export async function apiGet<T>(path: string, token?: string | null): Promise<T> {
  return (await apiRequest<T>(path, { token })).data;
}

/** SWR 키: [path, token?] */
export const swrFetcher = <T>([path, token]: [string, string | null | undefined]) => apiGet<T>(path, token);
export const swrEnvelope = <T>([path, token]: [string, string | null | undefined]) => apiRequest<T>(path, { token });
