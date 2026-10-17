/** Fired after every write request, so views such as the credit balance can refresh. */
export const MUTATION_EVENT = 'vo3:mutation';

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/v1' + path, {
    ...init,
    headers: {
      ...(init?.body && !(init.body instanceof FormData)
        ? { 'Content-Type': 'application/json' }
        : {}),
      ...init?.headers,
    },
  });
  if (init?.method && init.method !== 'GET' && typeof window !== 'undefined')
    window.dispatchEvent(new Event(MUTATION_EVENT));
  const body = await response.text();
  let data: unknown;
  try {
    data = body ? JSON.parse(body) : {};
  } catch {
    data = null;
  }
  if (!response.ok) {
    const detail =
      data && typeof data === 'object' && 'detail' in data && typeof data.detail === 'string'
        ? data.detail
        : `Máy chủ gặp lỗi (${response.status}). Hãy thử lại sau.`;
    const error = new Error(detail) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  if (data === null) throw new Error('Máy chủ trả về dữ liệu không hợp lệ.');
  return data as T;
}
