import { searchPubmed } from '$lib/api.pubmed';
import { loadRemoteSearch } from '$lib/server/remote-search-load';
import type { PubMedSearchResult } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, url }) => {
	const query = url.searchParams.get('q')?.trim() ?? '';
	return {
		query,
		result: await loadRemoteSearch<PubMedSearchResult>(query, () => searchPubmed(query, 25, fetch))
	};
};
