import { getArticle } from '$lib/api.pubmed';
import { critical } from '$lib/server/critical-load';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => ({
	article: await critical(getArticle(params.pmid, fetch))
});
