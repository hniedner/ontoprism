import { error } from '@sveltejs/kit';
import { searchClinicalTrials } from '$lib/api.clinicaltrials';
import { loadRemoteSearch } from '$lib/server/remote-search-load';
import { parseCursorGridUrl } from '$lib/server/repository-load';
import type { CTStudySearchPage } from '$lib/types';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, url }) => {
	const state = parseCursorGridUrl(url, {
		filters: {
			status: ['ACTIVE_NOT_RECRUITING', 'APPROVED_FOR_MARKETING', 'AVAILABLE', 'COMPLETED', 'ENROLLING_BY_INVITATION', 'NOT_YET_RECRUITING', 'NO_LONGER_AVAILABLE', 'RECRUITING', 'SUSPENDED', 'TEMPORARILY_NOT_AVAILABLE', 'TERMINATED', 'UNKNOWN', 'WITHDRAWN', 'WITHHELD'],
			phase: ['EARLY_PHASE1', 'PHASE1', 'PHASE2', 'PHASE3', 'PHASE4']
		} as const
	});
	const cursor = state.cursors.at(-1) ?? null;
	const result = await loadRemoteSearch<CTStudySearchPage>(state.query, () => searchClinicalTrials({ condition: state.query, limit: state.size, page_token: cursor, status: state.filters.status, phase: state.filters.phase }, fetch));
	if (result.state === 'ready' && (result.data.page_size !== state.size || result.data.page_token !== cursor || result.data.studies.length > result.data.total)) error(502, 'ClinicalTrials.gov page metadata did not match the request.');
	return { query: state.query, size: state.size, cursors: state.cursors, filters: state.filters, result };
};
