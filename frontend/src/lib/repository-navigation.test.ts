import { describe, expect, it } from 'vitest';
import { repositoryGridHref } from './repository-navigation';

describe('repositoryGridHref', () => {
	it('updates canonical repository URL state without mutating the current URL', () => {
		const current = new URL('https://example.test/repositories/pubmed?q=cancer&offset=25');
		const href = repositoryGridHref('/repositories/pubmed', current, (params) => {
			params.delete('offset');
			params.set('sort', 'pub_date');
		});

		expect(href).toBe('/repositories/pubmed?q=cancer&sort=pub_date');
		expect(current.search).toBe('?q=cancer&offset=25');
	});

	it('omits an empty query string', () => {
		expect(repositoryGridHref('/repositories/clinicaltrials', new URL('https://example.test/repositories/clinicaltrials'), () => {})).toBe('/repositories/clinicaltrials');
	});
});
