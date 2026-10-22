// 백엔드 API (SPEC 10). 응답 envelope {data, meta} 를 벗겨 data 만 돌려준다.
const BASE = `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/v1`;

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store", ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(res.status, body?.error?.code ?? "HTTP_ERROR", body?.error?.message ?? res.statusText);
  }
  return body.data as T;
}

/** API 가 꺼져 있어도 화면을 볼 수 있도록 실패 시 대체값을 쓴다. source 로 출처를 표시한다. */
export async function withFallback<T>(load: () => Promise<T>, fallback: () => T): Promise<{ data: T; live: boolean }> {
  try {
    return { data: await load(), live: true };
  } catch {
    return { data: fallback(), live: false };
  }
}
