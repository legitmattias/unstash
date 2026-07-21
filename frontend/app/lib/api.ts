import "server-only";
import { cookies } from "next/headers";

// Server-side calls reach FastAPI over the internal Docker network; the
// session cookie from the incoming request is forwarded so the API sees
// the same authenticated user. The browser reaches the API same-origin
// through Caddy, so client-side code uses plain relative "/api/..." fetches.
const API_ORIGIN = process.env.API_INTERNAL_ORIGIN ?? "http://localhost:8000";

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const cookieHeader = (await cookies()).toString();
  return fetch(`${API_ORIGIN}${path}`, {
    ...init,
    headers: { ...init?.headers, cookie: cookieHeader },
    cache: "no-store",
  });
}

export interface SearchResultItem {
  readonly document_id: string;
  readonly title: string;
  readonly mime_type: string;
  readonly chunk_id: string;
  readonly excerpt: string;
  readonly snippet: string;
  readonly score: number;
  readonly rerank_score: number | null;
}

export interface SearchResponse {
  readonly search_id: string;
  readonly query: string;
  readonly results: readonly SearchResultItem[];
  readonly result_count: number;
  readonly latency_ms: number;
  readonly reranked: boolean;
  readonly bm25_used: boolean;
}
