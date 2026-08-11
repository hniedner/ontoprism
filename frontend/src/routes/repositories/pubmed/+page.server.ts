import { searchPubmed } from '$lib/api.pubmed';
import { empty, ready } from '$lib/load-result';
import { critical } from '$lib/server/critical-load';
import type { PubMedSearchResult } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, url }) => {
	const query = url.searchParams.get('q')?.trim() ?? '';
	if (!query) return { query, result: empty<PubMedSearchResult>() };
	return { query, result: ready(await critical(searchPubmed(query, 25, fetch))) };
};
