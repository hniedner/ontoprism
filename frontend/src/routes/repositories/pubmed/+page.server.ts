import { error, redirect } from '@sveltejs/kit';
import { searchPubmed } from '$lib/api.pubmed';
import { loadRemoteSearch } from '$lib/server/remote-search-load';
import { canonicalOffsetGridSearch, parseOffsetGridUrl } from '$lib/server/repository-load';
import type { PubMedSearchResult } from '$lib/types';
import type { PageServerLoad } from './$types';

const spec = { defaultSort: 'relevance', sorts: ['relevance', 'pub_date'], filters: {} } as const;
export const load: PageServerLoad = async ({ fetch, url }) => {
	const { query, state } = parseOffsetGridUrl(url, spec);
	const result = await loadRemoteSearch<PubMedSearchResult>(query, () => searchPubmed(query, state.size, state.offset, state.sort as 'relevance' | 'pub_date', fetch));
	if (result.state === 'ready') {
		if (result.data.limit !== state.size || result.data.offset !== state.offset || result.data.sort !== state.sort) error(502, 'PubMed page metadata did not match the request.');
		const ceiling = Math.min(result.data.total, 10_000);
		const finalOffset = ceiling === 0 ? 0 : Math.floor((ceiling - 1) / state.size) * state.size;
		if (state.offset > finalOffset) { const search = canonicalOffsetGridSearch(query, { ...state, offset: finalOffset }, spec); throw redirect(307, `${url.pathname}?${search}`); }
	}
	return { query, size: state.size, offset: state.offset, sort: state.sort, result };
};
