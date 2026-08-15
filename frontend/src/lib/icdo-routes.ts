import type { IcdoDetail, IcdoPage } from '$lib/types';

export type IcdoDataset =
	| { edition: '3.2'; axis: 'morphology' }
	| { edition: '4.0'; axis: 'morphology' }
	| { edition: '4.0'; axis: 'topography' };

export function parseIcdoDataset(edition: string, axis: string): IcdoDataset | null {
	if (edition === '3.2' && axis === 'morphology') return { edition, axis };
	if (edition === '4.0' && axis === 'morphology') return { edition, axis };
	if (edition === '4.0' && axis === 'topography') return { edition, axis };
	return null;
}

export function icdoListPath(dataset: IcdoDataset): string {
	return `/api/v1/icdo/${dataset.edition}/${dataset.axis}/list`;
}

export function icdoSearchPath(dataset: IcdoDataset): string {
	return `/api/v1/icdo/${dataset.edition}/${dataset.axis}/search`;
}

export function icdoDetailPath(dataset: IcdoDataset, code: string): string {
	return `/api/v1/icdo/${dataset.edition}/${dataset.axis}/concepts/${encodeURIComponent(code)}`;
}

export function ncitMappingsPath(code: string): string {
	return `/api/v1/ncit/concepts/${encodeURIComponent(code)}/mappings`;
}

export function ncitDecompositionPath(code: string): string {
	return `/api/v1/ncit/concepts/${encodeURIComponent(code)}/decomposition`;
}

const ICDO_EXACT_PROTECTED_PATHS = new Set([
	'/api/v1/icdo/access',
	'/api/v1/icdo/4.0/topography/congruence',
	'/api/v1/mappings/$translate',
	'/api/v1/refresh'
]);

const ICDO_PROTECTED_PATH_PATTERNS = [
	/^\/api\/v1\/icdo\/(?:3\.2\/morphology|4\.0\/(?:morphology|topography))\/(?:metadata|list|search|concepts\/[^/]+)$/,
	/^\/api\/v1\/ncit\/concepts\/[^/]+\/(?:mappings|decomposition)$/
] as const;

export function isIcdoProtectedPath(pathname: string): boolean {
	return (
		ICDO_EXACT_PROTECTED_PATHS.has(pathname) ||
		ICDO_PROTECTED_PATH_PATTERNS.some((pattern) => pattern.test(pathname))
	);
}

export type IcdoPageFor<D extends IcdoDataset> = Extract<
	IcdoPage,
	{ edition: D['edition']; axis: D['axis'] }
>;

export type IcdoDetailFor<D extends IcdoDataset> = Extract<
	IcdoDetail,
	{ edition: D['edition']; axis: D['axis'] }
>;
