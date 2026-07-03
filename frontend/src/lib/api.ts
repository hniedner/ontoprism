// Typed client for the ontoprism backend. In dev, requests hit `/api/...` and Vite
// proxies them to the FastAPI backend (see vite.config.ts).

import type { ConceptDetail, Neighborhood, SearchPage } from './types';

const BASE = '';

/** Build an API URL with query params (pure — unit tested). */
export function apiUrl(path: string, params: Record<string, string | number> = {}): string {
	const qs = new URLSearchParams(
		Object.entries(params).map(([k, v]) => [k, String(v)])
	).toString();
	return `${BASE}${path}${qs ? `?${qs}` : ''}`;
}

async function getJson<T>(url: string, fetchImpl: typeof fetch = fetch): Promise<T> {
	const resp = await fetchImpl(url);
	if (!resp.ok) {
		throw new Error(`Request failed (${resp.status}): ${url}`);
	}
	return (await resp.json()) as T;
}

export function searchNcit(
	q: string,
	opts: { limit?: number; offset?: number; fetch?: typeof fetch } = {}
): Promise<SearchPage> {
	const url = apiUrl('/api/v1/ncit/search', {
		q,
		limit: opts.limit ?? 25,
		offset: opts.offset ?? 0
	});
	return getJson<SearchPage>(url, opts.fetch);
}

export function getConcept(code: string, fetchImpl?: typeof fetch): Promise<ConceptDetail> {
	return getJson<ConceptDetail>(apiUrl(`/api/v1/ncit/concepts/${encodeURIComponent(code)}`), fetchImpl);
}

export function getNeighborhood(
	code: string,
	depth = 1,
	fetchImpl?: typeof fetch
): Promise<Neighborhood> {
	return getJson<Neighborhood>(
		apiUrl(`/api/v1/ncit/concepts/${encodeURIComponent(code)}/neighborhood`, { depth }),
		fetchImpl
	);
}
