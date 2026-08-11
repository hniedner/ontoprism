import { listNcit, searchNcit } from '$lib/api';
import { critical } from '$lib/server/critical-load';
import { loadRepositoryPage } from '$lib/server/repository-load';
import type { PageServerLoad } from './$types';
import type { RepresentationStatus, SearchPage } from '$lib/types';
import { parseRepresentationStatus } from '$lib/representation-status';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ fetch, url }) => {
	return loadRepositoryPage<
		SearchPage,
		{ representationStatus: RepresentationStatus | null }
	>(
		url,
		(query, offset, state) =>
			critical(
				searchNcit(query, {
					limit: PAGE_SIZE,
					offset,
					representationStatus: state?.representationStatus ?? undefined,
					fetch
				})
			),
		(offset, state) =>
			critical(
				listNcit({
					limit: PAGE_SIZE,
					offset,
					representationStatus: state?.representationStatus ?? undefined,
					fetch
				})
			),
		(params): { representationStatus: RepresentationStatus | null } => ({
			representationStatus: parseRepresentationStatus(params)
		})
	);
};
