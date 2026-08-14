import { error } from '@sveltejs/kit';
import { getJson } from '$lib/api';
import {
	icdoDetailPath,
	parseIcdoDataset,
	type IcdoDetailFor
} from '$lib/icdo-routes';
import { critical } from '$lib/server/critical-load';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
	const dataset = parseIcdoDataset(params.edition, params.axis);
	if (!dataset) error(404, 'ICD-O dataset not found.');
	const detail = await critical(
		getJson<IcdoDetailFor<typeof dataset>>(icdoDetailPath(dataset, params.code), fetch)
	);
	return { ...detail, alignments: detail.ncit_alignments };
};
