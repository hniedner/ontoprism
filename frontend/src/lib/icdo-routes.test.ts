import { describe, expect, it } from 'vitest';

import {
	icdoListPath,
	icdoSearchPath,
	icdoDetailPath,
	isIcdoProtectedPath,
	parseIcdoDataset
} from './icdo-routes';

describe('ICD-O route contracts', () => {
	it.each([
		['3.2', 'morphology'],
		['4.0', 'morphology'],
		['4.0', 'topography']
	] as const)('parses served dataset %s/%s', (edition, axis) => {
		const dataset = parseIcdoDataset(edition, axis);

		expect(dataset).toEqual({ edition, axis });
		expect(icdoListPath(dataset!)).toBe(`/api/v1/icdo/${edition}/${axis}/list`);
		expect(icdoSearchPath(dataset!)).toBe(`/api/v1/icdo/${edition}/${axis}/search`);
		expect(icdoDetailPath(dataset!, '8503/0')).toBe(
			`/api/v1/icdo/${edition}/${axis}/concepts/8503%2F0`
		);
	});

	it.each([
		['3.2', 'topography'],
		['5.0', 'morphology'],
		['4.0', 'unknown']
	])('rejects unserved dataset %s/%s', (edition, axis) => {
		expect(parseIcdoDataset(edition, axis)).toBeNull();
	});

	it('classifies every protected route family without matching public routes', () => {
		for (const path of [
			'/api/v1/icdo/access',
			'/api/v1/icdo/4.0/topography/congruence',
			'/api/v1/mappings/$translate',
			'/api/v1/refresh',
			'/api/v1/icdo/3.2/morphology/metadata',
			'/api/v1/icdo/4.0/topography/list',
			'/api/v1/icdo/4.0/morphology/search',
			'/api/v1/icdo/3.2/morphology/concepts/ODUwMy8w',
			'/api/v1/ncit/concepts/C1234/mappings',
			'/api/v1/ncit/concepts/C1234/decomposition'
		]) {
			expect(isIcdoProtectedPath(path), path).toBe(true);
		}
		for (const path of [
			'/api/v1/ncit/list',
			'/api/v1/ncit/concepts/C6135/enhanced-ncit-showcase',
			'/api/v1/icdo/3.2/topography/list',
			'/api/v1/icdo/4.0/topography/unknown'
		]) {
			expect(isIcdoProtectedPath(path), path).toBe(false);
		}
	});
});
