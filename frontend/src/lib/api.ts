// Typed client for the ontoprism backend. In dev, requests hit `/api/...` and Vite
// proxies them to the FastAPI backend (see vite.config.ts).

import type {
	CdeDetail,
	CdeSearchPage,
	CdeSummary,
	ConceptDetail,
	Neighborhood,
	RefreshReport,
	SearchPage,
	SimilarCde,
	SimilarConcept,
	SparqlResponse
} from './types';

const BASE = '';

/** Build an API URL with query params (pure — unit tested). */
export function apiUrl(path: string, params: Record<string, string | number> = {}): string {
	const qs = new URLSearchParams(
		Object.entries(params).map(([k, v]) => [k, String(v)])
	).toString();
	return `${BASE}${path}${qs ? `?${qs}` : ''}`;
}

export async function getJson<T>(url: string, fetchImpl: typeof fetch = fetch): Promise<T> {
	const resp = await fetchImpl(url);
	if (!resp.ok) {
		throw new Error(`Request failed (${resp.status}): ${url}`);
	}
	return (await resp.json()) as T;
}

async function postJson<T>(url: string, fetchImpl: typeof fetch = fetch): Promise<T> {
	const resp = await fetchImpl(url, { method: 'POST' });
	if (!resp.ok) {
		throw new Error(`Request failed (${resp.status}): ${url}`);
	}
	return (await resp.json()) as T;
}

export async function postJsonBody<T>(
	url: string,
	body: unknown,
	fetchImpl: typeof fetch = fetch
): Promise<T> {
	const resp = await fetchImpl(url, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
	if (!resp.ok) {
		let detail = '';
		try {
			// `detail` is a string for our HTTPExceptions; FastAPI validation errors make
			// it an array — only use it when it's actually a string.
			const body = (await resp.json()) as { detail?: unknown };
			if (typeof body.detail === 'string') detail = body.detail;
		} catch {
			// non-JSON error body — fall through to the status-code message
		}
		throw new Error(detail || `Request failed (${resp.status}): ${url}`);
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

/** List NCIt concepts in natural order (no search term) — browse mode. */
export function listNcit(
	opts: { limit?: number; offset?: number; fetch?: typeof fetch } = {}
): Promise<SearchPage> {
	const url = apiUrl('/api/v1/ncit/list', {
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

/** Run a guarded read-only SPARQL query against the NCIt store. */
export function runSparql(query: string, fetchImpl?: typeof fetch): Promise<SparqlResponse> {
	return postJsonBody<SparqlResponse>(apiUrl('/api/v1/sparql'), { query }, fetchImpl);
}

/** CDE-centred subgraph joining the CDE into the NCIt concept graph. */
export function getCdeNeighborhood(
	publicId: string,
	depth = 1,
	fetchImpl?: typeof fetch
): Promise<Neighborhood> {
	return getJson<Neighborhood>(
		apiUrl(`/api/v1/cadsr/cdes/${encodeURIComponent(publicId)}/neighborhood`, { depth }),
		fetchImpl
	);
}

// --- caDSR ---

export function searchCadsr(
	q: string,
	opts: { limit?: number; offset?: number; fetch?: typeof fetch } = {}
): Promise<CdeSearchPage> {
	const url = apiUrl('/api/v1/cadsr/search', {
		q,
		limit: opts.limit ?? 25,
		offset: opts.offset ?? 0
	});
	return getJson<CdeSearchPage>(url, opts.fetch);
}

/** List caDSR CDEs in natural order (no search term) — browse mode. */
export function listCadsr(
	opts: { limit?: number; offset?: number; fetch?: typeof fetch } = {}
): Promise<CdeSearchPage> {
	const url = apiUrl('/api/v1/cadsr/list', {
		limit: opts.limit ?? 25,
		offset: opts.offset ?? 0
	});
	return getJson<CdeSearchPage>(url, opts.fetch);
}

export function getCde(
	publicId: string,
	version?: string,
	fetchImpl?: typeof fetch
): Promise<CdeDetail> {
	const params: Record<string, string | number> = version ? { version } : {};
	return getJson<CdeDetail>(
		apiUrl(`/api/v1/cadsr/cdes/${encodeURIComponent(publicId)}`, params),
		fetchImpl
	);
}

/** CDEs mapped to an NCIt concept — the caDSR↔NCIt cross-link. */
export function cdesForConcept(
	conceptCode: string,
	limit = 25,
	fetchImpl?: typeof fetch
): Promise<CdeSummary[]> {
	return getJson<CdeSummary[]>(
		apiUrl(`/api/v1/cadsr/concepts/${encodeURIComponent(conceptCode)}/cdes`, { limit }),
		fetchImpl
	);
}

// --- semantic similarity (embeddings) ---

export function similarConcepts(
	code: string,
	limit = 10,
	fetchImpl?: typeof fetch
): Promise<SimilarConcept[]> {
	return getJson<SimilarConcept[]>(
		apiUrl(`/api/v1/ncit/concepts/${encodeURIComponent(code)}/similar`, { limit }),
		fetchImpl
	);
}

export function similarCdes(
	publicId: string,
	limit = 10,
	fetchImpl?: typeof fetch
): Promise<SimilarCde[]> {
	return getJson<SimilarCde[]>(
		apiUrl(`/api/v1/cadsr/cdes/${encodeURIComponent(publicId)}/similar`, { limit }),
		fetchImpl
	);
}

// --- refresh ---

/** Re-probe repositories and return their live version/counts. */
export function refreshRepositories(fetchImpl?: typeof fetch): Promise<RefreshReport> {
	return postJson<RefreshReport>(apiUrl('/api/v1/refresh'), fetchImpl);
}
