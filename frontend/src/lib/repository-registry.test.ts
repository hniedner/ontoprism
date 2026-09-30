import { describe, expect, it, vi } from 'vitest';

import manifest from '../../../repository-manifest.json';
import { repositories, gridCapabilities } from './repository-registry';

describe('repository registry', () => {
	it('derives declared controls and rejects rendering or contradictory capabilities', async () => {
		expect(gridCapabilities('ncit').filters.representation_status.values).toEqual({ 'legacy-precoordinated': 'Legacy pre-coordinated' });
		for (const edit of [
			(c: Record<string, unknown>) => { c.columns = []; },
			(c: Record<string, unknown>) => { c.metadata = 'remote'; },
			(c: Record<string, unknown>) => { c.sorts = { list: [], search: ['relevance'] }; },
			(c: Record<string, unknown>) => { c.filters = { code: { kind: 'text', text_parameter: 'code_text', values: { C1: 'Concept' } } }; }
		]) {
			const input = structuredClone(manifest);
			edit(input[0].capabilities!);
			vi.resetModules();
			vi.doMock('../../../repository-manifest.json', () => ({ default: input }));
			try { await expect(import('./repository-registry')).rejects.toThrow(); }
			finally { vi.doUnmock('../../../repository-manifest.json'); vi.resetModules(); }
		}
	});
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

	it('requires every local-certified manifest entry in readiness representation', () => {
		const represented = new Set(['ncit', 'cadsr', 'uberon', 'icdo']);
		const declared = repositories
			.filter((entry) => entry.kind === 'local-certified-proxy')
			.map((entry) => entry.id);
		expect(declared.every((id) => represented.has(id))).toBe(true);
	});

});
