import { error } from '@sveltejs/kit';
import { getJson } from '#lib/api.ts';
import {
	icdoDetailPath,
	parseIcdoDataset
} from '#lib/icdo-routes.ts';
import { critical } from '#lib/server/critical-load.ts';
import type { IcdoDetail } from '#lib/types.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
	const dataset = parseIcdoDataset(params.edition, params.axis);
	if (!dataset) error(404, 'ICD-O dataset not found.');
	const detail = await critical(
		getJson<IcdoDetail>(icdoDetailPath(dataset, params.code), fetch)
	);
	return { ...detail, alignments: detail.ncit_alignments };
};
