import { listUberon, searchUberon } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage } from '$lib/server/repository-load';
import type { UberonSearchPage, UberonSource } from '$lib/types';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 25;

function sourceFrom(params: URLSearchParams): UberonSource | null {
	const source = params.get('source');
	return source === 'uberon' || source === 'cl' ? source : null;
}

export const load: PageServerLoad = async ({ fetch, url }) =>
	loadRepositoryPage<UberonSearchPage, { source: UberonSource | null }>(
		url,
		(query, offset, state) =>
			critical(
				searchUberon(query, {
					limit: PAGE_SIZE,
					offset,
					source: state?.source ?? undefined,
					fetch
				})
			),
		(offset, state) =>
			critical(
				listUberon({
					limit: PAGE_SIZE,
					offset,
					source: state?.source ?? undefined,
					fetch
				})
			),
		(params) => ({ source: sourceFrom(params) })
	);
