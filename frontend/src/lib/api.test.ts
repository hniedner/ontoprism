import { describe, expect, it, vi } from 'vitest';
import { apiUrl } from './api';
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
