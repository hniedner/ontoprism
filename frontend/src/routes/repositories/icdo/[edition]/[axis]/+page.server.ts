import { error } from '@sveltejs/kit';
import { listIcdo, searchIcdo } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import type { IcdoAxis, IcdoEdition } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params, url }) => {
	const edition = params.edition as IcdoEdition;
	const axis = params.axis as IcdoAxis;
	if (!(['3.2', '4.0'].includes(edition)) || !(['morphology', 'topography'].includes(axis)) || (edition === '3.2' && axis === 'topography')) error(404, 'ICD-O dataset not found.');
	const query = url.searchParams.get('q')?.trim() ?? '';
	const offset = Math.max(0, Number(url.searchParams.get('offset')) || 0);
	const behaviour = url.searchParams.get('behaviour') ?? undefined;
	const level = url.searchParams.get('level') ?? undefined;
	const result = await critical(query ? searchIcdo(edition, axis, query, { offset, behaviour, level, fetch })
		: listIcdo(edition, axis, { offset, behaviour, level, fetch }));
	return { edition, axis, query, behaviour, level, result };
};
