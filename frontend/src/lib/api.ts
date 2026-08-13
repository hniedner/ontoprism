// Typed same-origin client for the SvelteKit `/api` BFF. The BFF is the only frontend
// transport to FastAPI in development and in the built adapter-node server.

import type {
	CdeDetail,
	CdeSearchPage,
	CdeSummary,
	ConceptDecomposition,
	ConceptDetail,
	ConceptMappings,
	Neighborhood,
	RepresentationStatus,
	RefreshReport,
	SearchPage,
	SimilarCde,
	SimilarConcept
	, UberonConceptDetail
	, UberonAlignments
	, UberonNeighborhood
	, UberonSearchPage
	, UberonSource
} from './types';

const BASE = '';

export class ApiRequestError extends Error {
	constructor(
		readonly status: number,
		message: string,
		readonly remoteState?: RemoteFailureState
	) {
		super(message);
		this.name = 'ApiRequestError';
	}
}

export type RemoteFailureState = 'unavailable' | 'timeout' | 'rate-limited';

function remoteFailure(detail: unknown): { state: RemoteFailureState; message: string } | null {
	if (typeof detail !== 'object' || detail === null) return null;
	const value = detail as Record<string, unknown>;
	if (
		(value.state === 'unavailable' || value.state === 'timeout' || value.state === 'rate-limited') &&
		typeof value.message === 'string' &&
		value.message.trim()
	) {
		return { state: value.state, message: value.message };
	}
	return null;
}

async function failedResponse(response: Response, url: string): Promise<ApiRequestError> {
	let detail: unknown;
	try {
		detail = ((await response.json()) as { detail?: unknown }).detail;
	} catch {
		// A non-JSON upstream error still has an unambiguous HTTP status.
	}
	const remote = remoteFailure(detail);
	if (remote) return new ApiRequestError(response.status, remote.message, remote.state);
	const message = typeof detail === 'string' && detail.trim() ? detail : `Request failed (${response.status}): ${url}`;
	return new ApiRequestError(response.status, message);
}

/** Build an API URL with query params (pure — unit tested). */
export function apiUrl(path: string, params: Record<string, string | number> = {}): string {
	const qs = new URLSearchParams(
		Object.entries(params).map(([k, v]) => [k, String(v)])
	).toString();
	return `${BASE}${path}${qs ? `?${qs}` : ''}`;
}

export async function getJson<T>(
	url: string,
	fetchImpl: typeof fetch = fetch,
	signal?: AbortSignal
): Promise<T> {
	const resp = signal ? await fetchImpl(url, { signal }) : await fetchImpl(url);
	if (!resp.ok) {
		throw await failedResponse(resp, url);
	}
	return (await resp.json()) as T;
}

async function postJson<T>(url: string, fetchImpl: typeof fetch = fetch): Promise<T> {
	const resp = await fetchImpl(url, { method: 'POST' });
	if (!resp.ok) {
		throw await failedResponse(resp, url);
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
		throw await failedResponse(resp, url);
	}
	return (await resp.json()) as T;
}

export function searchNcit(
	q: string,
	opts: {
		limit?: number;
		offset?: number;
		representationStatus?: RepresentationStatus;
		fetch?: typeof fetch;
	} = {}
): Promise<SearchPage> {
	const params: Record<string, string | number> = {
		q,
		limit: opts.limit ?? 25,
		offset: opts.offset ?? 0
	};
	if (opts.representationStatus) {
		params.representation_status = opts.representationStatus;
	}
	const url = apiUrl('/api/v1/ncit/search', params);
	return getJson<SearchPage>(url, opts.fetch);
}

/** List NCIt concepts in natural order (no search term) — browse mode. */
export function listNcit(
	opts: {
		limit?: number;
		offset?: number;
		representationStatus?: RepresentationStatus;
		fetch?: typeof fetch;
	} = {}
): Promise<SearchPage> {
	const params: Record<string, string | number> = {
		limit: opts.limit ?? 25,
		offset: opts.offset ?? 0
	};
	if (opts.representationStatus) {
		params.representation_status = opts.representationStatus;
	}
	const url = apiUrl('/api/v1/ncit/list', params);
	return getJson<SearchPage>(url, opts.fetch);
}

export function getConcept(code: string, fetchImpl?: typeof fetch): Promise<ConceptDetail> {
	return getJson<ConceptDetail>(apiUrl(`/api/v1/ncit/concepts/${encodeURIComponent(code)}`), fetchImpl);
}

export function getNeighborhood(
	code: string,
	depth = 1,
	fetchImpl?: typeof fetch,
	signal?: AbortSignal
): Promise<Neighborhood> {
	return getJson<Neighborhood>(
		apiUrl(`/api/v1/ncit/concepts/${encodeURIComponent(code)}/neighborhood`, { depth }),
		fetchImpl,
		signal
	);
}

export function searchUberon(
	q: string,
	opts: { limit?: number; offset?: number; source?: UberonSource; fetch?: typeof fetch } = {}
): Promise<UberonSearchPage> {
	const params: Record<string, string | number> = {
		q,
		limit: opts.limit ?? 25,
		offset: opts.offset ?? 0
	};
	if (opts.source) params.source = opts.source;
	return getJson<UberonSearchPage>(apiUrl('/api/v1/uberon/search', params), opts.fetch);
}

export function listUberon(
	opts: { limit?: number; offset?: number; source?: UberonSource; fetch?: typeof fetch } = {}
): Promise<UberonSearchPage> {
	const params: Record<string, string | number> = {
		limit: opts.limit ?? 25,
		offset: opts.offset ?? 0
	};
	if (opts.source) params.source = opts.source;
	return getJson<UberonSearchPage>(apiUrl('/api/v1/uberon/list', params), opts.fetch);
}

export function getUberonConcept(
	code: string,
	fetchImpl?: typeof fetch
): Promise<UberonConceptDetail> {
	return getJson<UberonConceptDetail>(
		apiUrl(`/api/v1/uberon/concepts/${encodeURIComponent(code)}`),
		fetchImpl
	);
}

export function getUberonAlignments(
	code: string,
	fetchImpl?: typeof fetch
): Promise<UberonAlignments> {
	return getJson<UberonAlignments>(
		apiUrl(`/api/v1/uberon/concepts/${encodeURIComponent(code)}/alignments`),
		fetchImpl
	);
}

export function getUberonNeighborhood(
	code: string,
	depth = 1,
	fetchImpl?: typeof fetch,
	signal?: AbortSignal
): Promise<UberonNeighborhood> {
	return getJson<UberonNeighborhood>(
		apiUrl(`/api/v1/uberon/concepts/${encodeURIComponent(code)}/neighborhood`, { depth }),
		fetchImpl,
		signal
	);
}

/** The concept's decomposition (constituents by axis + legacy flag) from ncit_decomposed. */
export function getDecomposition(
	code: string,
	fetchImpl?: typeof fetch,
	signal?: AbortSignal
): Promise<ConceptDecomposition> {
	return getJson<ConceptDecomposition>(
		apiUrl(`/api/v1/ncit/concepts/${encodeURIComponent(code)}/decomposition`),
		fetchImpl,
		signal
	);
}

/** All upstream mappings for an NCIt concept (both directions). */
export function getMappings(
	code: string,
	fetchImpl?: typeof fetch,
	signal?: AbortSignal
): Promise<ConceptMappings> {
	return getJson<ConceptMappings>(
		apiUrl(`/api/v1/ncit/concepts/${encodeURIComponent(code)}/mappings`),
		fetchImpl,
		signal
	);
}

/** CDE-centred subgraph joining the CDE into the NCIt concept graph. */
export function getCdeNeighborhood(
	publicId: string,
	depth = 1,
	fetchImpl?: typeof fetch,
	signal?: AbortSignal
): Promise<Neighborhood> {
	return getJson<Neighborhood>(
		apiUrl(`/api/v1/cadsr/cdes/${encodeURIComponent(publicId)}/neighborhood`, { depth }),
		fetchImpl,
		signal
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
	fetchImpl?: typeof fetch,
	signal?: AbortSignal
): Promise<CdeSummary[]> {
	return getJson<CdeSummary[]>(
		apiUrl(`/api/v1/cadsr/concepts/${encodeURIComponent(conceptCode)}/cdes`, { limit }),
		fetchImpl,
		signal
	);
}

// --- semantic similarity (embeddings) ---

export function similarConcepts(
	code: string,
	limit = 10,
	fetchImpl?: typeof fetch,
	signal?: AbortSignal
): Promise<SimilarConcept[]> {
	return getJson<SimilarConcept[]>(
		apiUrl(`/api/v1/ncit/concepts/${encodeURIComponent(code)}/similar`, { limit }),
		fetchImpl,
		signal
	);
}

export function similarCdes(
	publicId: string,
	limit = 10,
	fetchImpl?: typeof fetch,
	signal?: AbortSignal
): Promise<SimilarCde[]> {
	return getJson<SimilarCde[]>(
		apiUrl(`/api/v1/cadsr/cdes/${encodeURIComponent(publicId)}/similar`, { limit }),
		fetchImpl,
		signal
	);
}

// --- refresh ---

/** Re-probe repositories and return their live version/counts. */
export function refreshRepositories(fetchImpl?: typeof fetch): Promise<RefreshReport> {
	return postJson<RefreshReport>(apiUrl('/api/v1/refresh'), fetchImpl);
}
