import { describe, expect, it } from 'vitest';
import { repositorySearchHref } from './repository-search';

describe('repositorySearchHref', () => {
	it('sets a trimmed query while preserving unrelated URL state', () => {
		expect(
			repositorySearchHref(
				'clinicaltrials',
				new URL('http://example.test/repositories/clinicaltrials?view=compact'),
				'  melanoma  '
			)
		).toBe('/repositories/clinicaltrials?view=compact&q=melanoma');
	});

	it('removes an empty query for PubMed', () => {
		expect(
			repositorySearchHref(
				'pubmed',
				new URL('http://example.test/repositories/pubmed?q=old'),
				'  '
			)
		).toBe('/repositories/pubmed');
	});
});
