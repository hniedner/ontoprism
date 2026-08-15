import { error } from '@sveltejs/kit';
import { listIcdo, searchIcdo } from '$lib/api';
import { parseIcdoDataset } from '$lib/icdo-routes';
import { critical } from '$lib/server/critical-load';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params, url }) => {
	const dataset = parseIcdoDataset(params.edition, params.axis);
	if (!dataset) error(404, 'ICD-O dataset not found.');
	const query = url.searchParams.get('q')?.trim() ?? '';
	const offset = Math.max(0, Number(url.searchParams.get('offset')) || 0);
	const behaviour = url.searchParams.get('behaviour') ?? undefined;
	const level = url.searchParams.get('level') ?? undefined;
	const result = await critical(query ? searchIcdo(dataset, query, { offset, behaviour, level, fetch })
		: listIcdo(dataset, { offset, behaviour, level, fetch }));
	return { ...dataset, query, behaviour, level, result };
};
