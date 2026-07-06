import { describe, expect, it, vi } from 'vitest';
import { apiUrl } from './api';
import { getTrial, searchClinicalTrials } from './api.clinicaltrials';

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
