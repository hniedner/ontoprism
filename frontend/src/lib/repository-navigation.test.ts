import { describe, expect, it } from 'vitest';
import { clearGridFilters, navigateRepositoryGrid } from './repository-navigation';

describe('navigateRepositoryGrid', () => {
	it('updates canonical repository URL state without mutating the current URL', () => {
		const current = new URL('https://example.test/repositories/pubmed?q=cancer&offset=25');
		let href = '';
		navigateRepositoryGrid('/repositories/pubmed', current, (params) => {
			params.delete('offset');
			params.set('sort', 'pub_date');
		}, (target) => { href = target; });

		expect(href).toBe('/repositories/pubmed?q=cancer&sort=pub_date');
		expect(current.search).toBe('?q=cancer&offset=25');
	});

	it('omits an empty query string', () => {
		let href = '';
		navigateRepositoryGrid('/repositories/clinicaltrials', new URL('https://example.test/repositories/clinicaltrials'), () => {}, (target) => { href = target; });
		expect(href).toBe('/repositories/clinicaltrials');
	});

	it('clears every filter key supplied by canonical loaded state', () => {
		const params = new URLSearchParams('q=cancer&status=RECRUITING&phase=PHASE2&future=value');
		clearGridFilters(params, ['status', 'phase', 'future']);
		expect(params.toString()).toBe('q=cancer');
	});
});
