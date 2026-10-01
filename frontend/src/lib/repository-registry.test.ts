import { describe, expect, it } from 'vitest';

import manifest from '../../../repository-manifest.json';
import { columnFilter, gridCapabilities, gridControls, repositories } from './repository-registry';

describe('repository registry', () => {
	it('derives declared controls from the tracked manifest', () => {
		expect(gridCapabilities('ncit').filters.representation_status.values).toEqual({ 'legacy-precoordinated': 'Legacy pre-coordinated' });
		expect(gridCapabilities('ncit').filters.semantic_type).toMatchObject({ multiple: true, source_domain: 'semantic-types' });
	});
	it('declares only the controls supported by each served ICD-O dataset', () => {
		expect(Object.keys(gridCapabilities('icdo', '3.2/morphology').filters)).toEqual(['code', 'preferred', 'behaviour']);
		expect(Object.keys(gridCapabilities('icdo', '4.0/topography').filters)).toEqual(['code', 'preferred', 'level']);
		expect(() => gridCapabilities('icdo', '3.2/topography')).toThrow('no grid declaration');
	});
	it('declares remote pagination and only upstream-supported controls', () => {
		expect(gridCapabilities('pubmed')).toMatchObject({ pagination: 'offset', query_before_results: true, metadata: 'remote', filters: {} });
		expect(gridCapabilities('clinicaltrials')).toMatchObject({ pagination: 'cursor', query_before_results: true, metadata: 'remote' });
		expect(gridControls('clinicaltrials').filters).toEqual({
			status: expect.arrayContaining(['RECRUITING', 'COMPLETED']),
			phase: expect.arrayContaining(['PHASE1', 'PHASE4'])
		});
		expect(columnFilter('clinicaltrials', 'status', 'Filter trial statuses')).toMatchObject({
			kind: 'categorical',
			textFilter: false
		});
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
