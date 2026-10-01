// Typed same-origin client for the SvelteKit `/api` BFF. The BFF is the only frontend
// transport to FastAPI in development and in the built adapter-node server.

import { gridCapabilities, type RepositoryId } from './repository-registry';
import type {
	CdeDetail,
	CadsrFilterDomains,
	CdeRepositorySort,
	CdeSearchPage,
	CdeSummary,
	IcdoBehaviour,
	IcdoRecordLevel,
	IcdoRepositorySort,
	ConceptDecomposition,
    ConstituentEvidence,
    DeltaOccurrence,
	PublicationProgress,
	ConceptDetail,
	ConceptAlignments,
	Neighborhood,
	NcitBrowsePage,
	NcitColumnText,
	NcitBrowseSort,
	NcitSearchPage,
	NcitSearchSort,
	RepresentationStatus,
	RefreshReport,
	SimilarCde,
	SimilarConcept
	, UberonConceptDetail
	, UberonAlignments
	, UberonNeighborhood
	, UberonBrowsePage
	, UberonBrowseSort
	, UberonColumnText
	, UberonSearchPage
	, UberonSearchSort
	, UberonSource
} from './types';
import {
	icdoListPath,
	icdoSearchPath,
	ncitDecompositionPath,
	decompositionPublicationProgressPath,
	ncitMappingsPath,
	type IcdoDataset,
	type IcdoPageFor
} from './icdo-routes';

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
export function apiUrl(path: string, params: Record<string, string | number | readonly string[]> = {}): string {
	const query = new URLSearchParams();
	for (const [key, raw] of Object.entries(params)) {
		for (const value of Array.isArray(raw) ? raw : [raw]) query.append(key, String(value));
	}
	const qs = query.toString();
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

function appendColumnText(params: Record<string, string | number | readonly string[]>, repository: RepositoryId, values: Partial<Record<string, string>> = {}, dataset?: string): void {
	for (const [column, value] of Object.entries(values)) {
		const filter = gridCapabilities(repository, dataset).filters[column];
		if (!filter) throw new Error(`Unknown ${repository} text column: ${column}`);
		if (value === undefined) continue;
		params[filter.text_parameter] = value;
	}
}

export function searchNcit(
	q: string,
	opts: {
		limit?: number;
		offset?: number;
		representationStatus?: RepresentationStatus;
		sort?: NcitSearchSort;
		columnText?: NcitColumnText;
		semanticTypes?: string[];
		fetch?: typeof fetch;
	} = {}
): Promise<NcitSearchPage> {
	const url = ncitUrl('/api/v1/ncit/search', { q, ...ncitGridParams(opts) }, opts.semanticTypes);
	return getJson<NcitSearchPage>(url, opts.fetch);
}

function ncitGridParams(opts: { limit?: number; offset?: number; representationStatus?: RepresentationStatus; sort?: string; columnText?: NcitColumnText }): Record<string, string | number> {
	const params: Record<string, string | number> = { limit: opts.limit ?? 25, offset: opts.offset ?? 0 };
	if (opts.representationStatus) params.representation_status = opts.representationStatus;
	if (opts.sort) params.sort = opts.sort;
	appendColumnText(params, 'ncit', opts.columnText);
	return params;
}

function ncitUrl(path: string, params: Record<string, string | number>, semanticTypes: string[] = []): string {
	const url = apiUrl(path, params);
	return url + semanticTypes.map((value) => `&semantic_type=${encodeURIComponent(value)}`).join('');
}

export function getNcitSemanticTypes(fetchImpl?: typeof fetch): Promise<string[]> {
	return getJson<string[]>(apiUrl('/api/v1/ncit/semantic-types'), fetchImpl);
}

/** List NCIt concepts in the requested deterministic browse order. */
export function listNcit(
	opts: {
		limit?: number;
		offset?: number;
		representationStatus?: RepresentationStatus;
		sort?: NcitBrowseSort;
		columnText?: NcitColumnText;
		semanticTypes?: string[];
		fetch?: typeof fetch;
	} = {}
): Promise<NcitBrowsePage> {
	const url = ncitUrl('/api/v1/ncit/list', ncitGridParams(opts), opts.semanticTypes);
	return getJson<NcitBrowsePage>(url, opts.fetch);
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

type UberonGridOptions<
	Sort extends UberonBrowseSort | UberonSearchSort = UberonBrowseSort | UberonSearchSort
> = {
	limit?: number;
	offset?: number;
	sources?: UberonSource[];
	sort?: Sort;
	columnText?: UberonColumnText;
	fetch?: typeof fetch;
};

function uberonGridParams(opts: UberonGridOptions): Record<string, string | number | readonly string[]> {
	const params: Record<string, string | number | readonly string[]> = {
		limit: opts.limit ?? 25,
		offset: opts.offset ?? 0
	};
	if (opts.sources?.length) params.source = opts.sources;
	if (opts.sort) params.sort = opts.sort;
	appendColumnText(params, 'uberon', opts.columnText);
	return params;
}

export function searchUberon(
	q: string,
	opts: UberonGridOptions<UberonSearchSort> = {}
): Promise<UberonSearchPage> {
	const params = { q, ...uberonGridParams(opts) };
	return getJson<UberonSearchPage>(apiUrl('/api/v1/uberon/search', params), opts.fetch);
}

export function listUberon(
	opts: UberonGridOptions<UberonBrowseSort> = {}
): Promise<UberonBrowsePage> {
	return getJson<UberonBrowsePage>(apiUrl('/api/v1/uberon/list', uberonGridParams(opts)), opts.fetch);
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

export function icdoCodeSegment(code: string): string {
	const bytes = new TextEncoder().encode(code);
	let binary = '';
	for (const byte of bytes) binary += String.fromCharCode(byte);
	return btoa(binary).replace(/=+$/, '');
}

interface IcdoGridOptions {
	limit?: number;
	offset?: number;
	behaviour?: readonly IcdoBehaviour[];
	level?: readonly IcdoRecordLevel[];
	sort?: IcdoRepositorySort;
	columnText?: Partial<Record<string, string>>;
	fetch?: typeof fetch;
}

function icdoGridParams(dataset: IcdoDataset, q: string | undefined, opts: IcdoGridOptions): Record<string, string | number | readonly string[]> {
	const params: Record<string, string | number | readonly string[]> = { limit: opts.limit ?? 25, offset: opts.offset ?? 0 };
	if (q !== undefined) params.q = q;
	if (opts.behaviour) params.behaviour = opts.behaviour;
	if (opts.level) params.level = opts.level;
	if (opts.sort) params.sort = opts.sort;
	appendColumnText(params, 'icdo', opts.columnText, `${dataset.edition}/${dataset.axis}`);
	return params;
}

export function listIcdo<D extends IcdoDataset>(dataset: D, opts: IcdoGridOptions = {}): Promise<IcdoPageFor<D>> {
	return getJson<IcdoPageFor<D>>(apiUrl(icdoListPath(dataset), icdoGridParams(dataset, undefined, opts)), opts.fetch);
}

export function searchIcdo<D extends IcdoDataset>(dataset: D, q: string, opts: IcdoGridOptions = {}): Promise<IcdoPageFor<D>> {
	return getJson<IcdoPageFor<D>>(apiUrl(icdoSearchPath(dataset), icdoGridParams(dataset, q, opts)), opts.fetch);
}


/** The concept's decomposition (constituents by axis + legacy flag) from ncit_decomposed. */
export function getDecomposition(
	code: string,
	fetchImpl?: typeof fetch,
	signal?: AbortSignal
): Promise<ConceptDecomposition> {
	return getJson<ConceptDecomposition>(
		apiUrl(ncitDecompositionPath(code)),
		fetchImpl,
		signal
	);
}

export function getConstituentEvidence(run: string, code: string, signal?: AbortSignal): Promise<ConstituentEvidence[]> {
    return getJson<ConstituentEvidence[]>(apiUrl(`/api/v1/decomposition/runs/${encodeURIComponent(run)}/concepts/${encodeURIComponent(code)}/evidence`), undefined, signal);
}

export function getEnhancementDelta(run: string, code: string, signal?: AbortSignal): Promise<DeltaOccurrence[]> {
    return getJson<DeltaOccurrence[]>(apiUrl(`/api/v1/decomposition/runs/${encodeURIComponent(run)}/concepts/${encodeURIComponent(code)}/delta`), undefined, signal);
}

export function getPublicationProgress(fetchImpl?: typeof fetch): Promise<PublicationProgress> {
	return getJson<PublicationProgress>(apiUrl(decompositionPublicationProgressPath()), fetchImpl);
}

/** All terminology alignments for an NCIt concept (both directions). */
export function getAlignments(
	code: string,
	fetchImpl?: typeof fetch,
	signal?: AbortSignal
): Promise<ConceptAlignments> {
	return getJson<ConceptAlignments>(
		apiUrl(ncitMappingsPath(code)),
		fetchImpl,
		signal
	);
}

/** CDE-centred subgraph joining the CDE into the NCIt concept graph. */
export function getCdeNeighborhood(
	publicId: string,
	fetchImpl?: typeof fetch,
	signal?: AbortSignal
): Promise<Neighborhood> {
	return getJson<Neighborhood>(
		apiUrl(`/api/v1/cadsr/cdes/${encodeURIComponent(publicId)}/neighborhood`),
		fetchImpl,
		signal
	);
}

// --- caDSR ---

interface CadsrGridOptions {
	limit?: number;
	offset?: number;
	sort?: CdeRepositorySort;
	filters?: Partial<Record<keyof CadsrFilterDomains, readonly string[]>>;
	columnText?: Partial<Record<string, string>>;
	fetch?: typeof fetch;
}

function cadsrGridParams(opts: CadsrGridOptions): Record<string, string | number | readonly string[]> {
	const params: Record<string, string | number | readonly string[]> = {
		limit: opts.limit ?? 25,
		offset: opts.offset ?? 0,
		sort: opts.sort ?? 'source'
	};
	for (const [key, values] of Object.entries(opts.filters ?? {})) if (values?.length) params[key] = values;
	appendColumnText(params, 'cadsr', opts.columnText);
	return params;
}

export function searchCadsr(
	q: string,
	opts: CadsrGridOptions = {}
): Promise<CdeSearchPage> {
	const url = apiUrl('/api/v1/cadsr/search', { q, ...cadsrGridParams(opts) });
	return getJson<CdeSearchPage>(url, opts.fetch);
}

/** List caDSR CDEs in the requested deterministic browse order. */
export function listCadsr(
	opts: CadsrGridOptions = {}
): Promise<CdeSearchPage> {
	const url = apiUrl('/api/v1/cadsr/list', cadsrGridParams(opts));
	return getJson<CdeSearchPage>(url, opts.fetch);
}

export function getCadsrFilterDomains(fetchImpl?: typeof fetch): Promise<CadsrFilterDomains> {
	return getJson<CadsrFilterDomains>(apiUrl('/api/v1/cadsr/filter-domains'), fetchImpl);
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

/** Re-certify local repositories, returning identities or typed refusal details. */
export function refreshRepositories(fetchImpl?: typeof fetch): Promise<RefreshReport> {
	return postJson<RefreshReport>(apiUrl('/api/v1/refresh'), fetchImpl);
}
