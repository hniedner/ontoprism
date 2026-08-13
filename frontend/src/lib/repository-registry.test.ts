import { describe, expect, it } from 'vitest';

import manifest from '../../../repository-manifest.json';
import { repositories } from './repository-registry';

describe('repository registry', () => {
	it('loads the tracked local-certified and remote-live descriptors', () => {
		expect(repositories).toEqual(manifest);
		expect(repositories.filter((entry) => entry.kind === 'local-certified-proxy').map((entry) => entry.id)).toEqual([
			'ncit',
			'cadsr',
			'uberon',
			'icdo'
		]);
		expect(repositories.filter((entry) => entry.kind === 'remote-live-service').map((entry) => entry.id)).toEqual([
			'clinicaltrials',
			'pubmed'
		]);
	});

});
