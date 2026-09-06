import { beforeEach, describe, expect, it, vi } from 'vitest';

const { searchClinicalTrials } = vi.hoisted(() => ({ searchClinicalTrials: vi.fn() }));
vi.mock('$lib/api.clinicaltrials', () => ({ searchClinicalTrials }));

import { load } from './+page.server';

const study = { nct_id: 'NCT00000001', title: 'Trial' };
function ready(overrides: Record<string, unknown> = {}) {
	return { condition: 'cancer', intervention: null, term: null, status: [], phase: [], total: 2, page_size: 25, page_token: null, next_page_token: 'next', studies: [study], ...overrides };
}

describe('ClinicalTrials.gov page server cursor contract', () => {
	beforeEach(() => searchClinicalTrials.mockReset());

	it('uses only the last opaque trail token and returns the complete canonical trail', async () => {
		searchClinicalTrials.mockResolvedValue(ready({ page_token: 'second' }));
		const result = await load({
			url: new URL('https://example.test/repositories/clinicaltrials?q=cancer&cursor=first&cursor=second'),
			fetch: vi.fn()
		} as never);

		expect(searchClinicalTrials).toHaveBeenCalledWith(expect.objectContaining({ page_token: 'second', limit: 25 }), expect.any(Function));
		expect(result).toMatchObject({ cursors: ['first', 'second'] });
	});

	it.each([
		['page size', { page_size: 50 }],
		['page token', { page_token: 'wrong' }],
		['status filters', { status: ['COMPLETED'] }],
		['phase filters', { phase: ['PHASE3'] }],
		['total', { total: 0, studies: [study] }]
	])('fails closed on %s response metadata drift', async (_label, overrides) => {
		searchClinicalTrials.mockResolvedValue(ready({ page_token: 'current', ...overrides }));
		await expect(load({
			url: new URL('https://example.test/repositories/clinicaltrials?q=cancer&cursor=current'),
			fetch: vi.fn()
		} as never)).rejects.toMatchObject({ status: 502 });
	});
});
