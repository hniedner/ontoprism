import { describe, expect, it, vi } from 'vitest';
import {
	apiUrl,
	searchNcit,
	listNcit,
	getConcept,
	getNeighborhood,
	getDecomposition,
	runSparql,
	getCdeNeighborhood,
	searchCadsr,
	listCadsr,
	getCde,
	cdesForConcept,
	similarConcepts,
	similarCdes,
	refreshRepositories
} from './api';
import { getTrial, searchClinicalTrials } from './api.clinicaltrials';
import { getArticle, getRelatedArticles, searchPubmed } from './api.pubmed';

describe('apiUrl', () => {
	it('returns the bare path when there are no params', () => {
		expect(apiUrl('/api/v1/ncit/concepts/C3262')).toBe('/api/v1/ncit/concepts/C3262');
	});

	it('appends and encodes query params', () => {
		expect(apiUrl('/api/v1/ncit/search', { q: 'small cell', limit: 10 })).toBe(
			'/api/v1/ncit/search?q=small+cell&limit=10'
		);
	});
});

function jsonResponse(body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'Content-Type': 'application/json' }
	});
}

describe('searchClinicalTrials', () => {
	it('POSTs the search request as a JSON body to the trials endpoint', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(
			jsonResponse({ condition: 'melanoma', intervention: null, term: null, total: 0, studies: [] })
		);
		const page = await searchClinicalTrials({ condition: 'melanoma', limit: 10 }, fetchImpl);
		expect(page.total).toBe(0);
		const [url, init] = fetchImpl.mock.calls[0];
		expect(url).toBe('/api/v1/clinicaltrials/search');
		expect(init.method).toBe('POST');
		expect(JSON.parse(init.body)).toEqual({ condition: 'melanoma', limit: 10 });
	});
});

describe('getTrial', () => {
	it('GETs the trial detail endpoint with the NCT id encoded', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ nct_id: 'NCT01234567', title: 'x' }));
		const detail = await getTrial('NCT01234567', fetchImpl);
		expect(detail.nct_id).toBe('NCT01234567');
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/clinicaltrials/NCT01234567');
	});
});

describe('searchPubmed', () => {
	it('POSTs the query + retmax to the pubmed search endpoint', async () => {
		const fetchImpl = vi
			.fn()
			.mockResolvedValue(jsonResponse({ query: 'melanoma', total: 0, articles: [] }));
		await searchPubmed('melanoma', 25, fetchImpl);
		const [url, init] = fetchImpl.mock.calls[0];
		expect(url).toBe('/api/v1/pubmed/search');
		expect(JSON.parse(init.body)).toEqual({ query: 'melanoma', retmax: 25 });
	});
});

describe('getArticle', () => {
	it('GETs the article endpoint with the PMID encoded', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ pmid: '111', title: 'x' }));
		const article = await getArticle('111', fetchImpl);
		expect(article.pmid).toBe('111');
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/pubmed/111');
	});
});

describe('getRelatedArticles', () => {
	it('GETs the related endpoint with the link_type query param', async () => {
		const fetchImpl = vi
			.fn()
			.mockResolvedValue(jsonResponse({ pmid: '111', link_type: 'similar', related_pmids: [] }));
		await getRelatedArticles('111', 'similar', fetchImpl);
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/pubmed/111/related?link_type=similar');
	});
});

describe('error handling', () => {
	it('surfaces the backend HTTPException `detail` string on a failed POST', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(
			new Response(JSON.stringify({ detail: 'Invalid trial phase filter.' }), {
				status: 400,
				headers: { 'Content-Type': 'application/json' }
			})
		);
		await expect(searchClinicalTrials({ condition: 'x' }, fetchImpl)).rejects.toThrow(
			'Invalid trial phase filter.'
		);
	});
});

describe('NCIt endpoints', () => {
	it('searchNcit builds the search URL with q/limit/offset defaults', async () => {
		const fetchImpl = vi
			.fn()
			.mockResolvedValue(jsonResponse({ query: 'neo', total: 0, limit: 25, offset: 0, hits: [] }));
		await searchNcit('neo', { fetch: fetchImpl });
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/ncit/search?q=neo&limit=25&offset=0');
	});

	it('listNcit builds the browse URL with explicit paging', async () => {
		const fetchImpl = vi
			.fn()
			.mockResolvedValue(jsonResponse({ query: '', total: 0, limit: 5, offset: 10, hits: [] }));
		await listNcit({ limit: 5, offset: 10, fetch: fetchImpl });
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/ncit/list?limit=5&offset=10');
	});

	it('getConcept encodes the code in the path', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ code: 'C 3262' }));
		await getConcept('C 3262', fetchImpl);
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/ncit/concepts/C%203262');
	});

	it('getNeighborhood passes the depth query param', async () => {
		const fetchImpl = vi
			.fn()
			.mockResolvedValue(jsonResponse({ center: 'C3262', nodes: [], edges: [] }));
		await getNeighborhood('C3262', 2, fetchImpl);
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/ncit/concepts/C3262/neighborhood?depth=2');
	});

	it('similarConcepts requests the similar endpoint with a limit', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(jsonResponse([]));
		await similarConcepts('C3262', 5, fetchImpl);
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/ncit/concepts/C3262/similar?limit=5');
	});

	it('getDecomposition requests the decomposition endpoint with the code encoded', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(
			jsonResponse({
				code: 'C6135',
				is_legacy_precoordinated: false,
				decomposed_on: null,
				constituents: []
			})
		);
		await getDecomposition('C 6135', fetchImpl);
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/ncit/concepts/C%206135/decomposition');
	});
});

describe('caDSR endpoints', () => {
	it('searchCadsr builds the search URL', async () => {
		const fetchImpl = vi
			.fn()
			.mockResolvedValue(jsonResponse({ query: 'age', total: 0, limit: 25, offset: 0, hits: [] }));
		await searchCadsr('age', { fetch: fetchImpl });
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/cadsr/search?q=age&limit=25&offset=0');
	});

	it('listCadsr builds the browse URL', async () => {
		const fetchImpl = vi
			.fn()
			.mockResolvedValue(jsonResponse({ query: '', total: 0, limit: 25, offset: 0, hits: [] }));
		await listCadsr({ fetch: fetchImpl });
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/cadsr/list?limit=25&offset=0');
	});

	it('getCde omits the version param when not given', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ public_id: '100' }));
		await getCde('100', undefined, fetchImpl);
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/cadsr/cdes/100');
	});

	it('getCde includes the version param when given', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ public_id: '100' }));
		await getCde('100', '2.0', fetchImpl);
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/cadsr/cdes/100?version=2.0');
	});

	it('cdesForConcept requests the concept→CDE cross-link with a limit', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(jsonResponse([]));
		await cdesForConcept('C3262', 50, fetchImpl);
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/cadsr/concepts/C3262/cdes?limit=50');
	});

	it('getCdeNeighborhood passes the depth param', async () => {
		const fetchImpl = vi
			.fn()
			.mockResolvedValue(jsonResponse({ center: 'cde:100:2.0', nodes: [], edges: [] }));
		await getCdeNeighborhood('100', 1, fetchImpl);
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/cadsr/cdes/100/neighborhood?depth=1');
	});

	it('similarCdes requests the similar endpoint with a limit', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(jsonResponse([]));
		await similarCdes('100', 8, fetchImpl);
		expect(fetchImpl.mock.calls[0][0]).toBe('/api/v1/cadsr/cdes/100/similar?limit=8');
	});
});

describe('sparql + refresh', () => {
	it('runSparql POSTs the query as a JSON body', async () => {
		const fetchImpl = vi
			.fn()
			.mockResolvedValue(jsonResponse({ result: { head: {}, results: {} }, truncated: false }));
		await runSparql('SELECT * WHERE { ?s ?p ?o }', fetchImpl);
		const [url, init] = fetchImpl.mock.calls[0];
		expect(url).toBe('/api/v1/sparql');
		expect(init.method).toBe('POST');
		expect(JSON.parse(init.body)).toEqual({ query: 'SELECT * WHERE { ?s ?p ?o }' });
	});

	it('refreshRepositories POSTs to the refresh endpoint', async () => {
		const fetchImpl = vi
			.fn()
			.mockResolvedValue(jsonResponse({ refreshed_at: 't', repositories: [] }));
		await refreshRepositories(fetchImpl);
		const [url, init] = fetchImpl.mock.calls[0];
		expect(url).toBe('/api/v1/refresh');
		expect(init.method).toBe('POST');
	});
});

describe('getJson error handling', () => {
	it('throws with the status code when a GET fails', async () => {
		const fetchImpl = vi.fn().mockResolvedValue(new Response('nope', { status: 404 }));
		await expect(getConcept('C0', fetchImpl)).rejects.toThrow('404');
	});
});
