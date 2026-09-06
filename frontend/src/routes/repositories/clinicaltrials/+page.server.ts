import { error } from '@sveltejs/kit';
import { searchClinicalTrials } from '$lib/api.clinicaltrials';
import { loadRemoteSearch } from '$lib/server/remote-search-load';
import { parseCursorGridUrl } from '$lib/server/repository-load';
import { CT_PHASES, CT_STATUSES, type CTStudySearchPage } from '$lib/types';
import type { PageServerLoad } from './$types';

function sameValues(left: readonly string[], right: readonly string[] | undefined): boolean {
	return left.length === (right?.length ?? 0) && left.every((value, index) => value === right?.[index]);
}

export const load: PageServerLoad = async ({ fetch, url }) => {
	const state = parseCursorGridUrl(url, {
		filters: {
			status: CT_STATUSES,
			phase: CT_PHASES
		}
	});
	const cursor = state.cursors.at(-1) ?? null;
	const result = await loadRemoteSearch<CTStudySearchPage>(state.query, () => searchClinicalTrials({ condition: state.query, limit: state.size, page_token: cursor, status: state.filters.status, phase: state.filters.phase }, fetch));
	if (result.state === 'ready' && (
		result.data.page_size !== state.size
		|| result.data.page_token !== cursor
		|| !sameValues(result.data.status, state.filters.status)
		|| !sameValues(result.data.phase, state.filters.phase)
		|| result.data.studies.length > result.data.total
	)) error(502, 'ClinicalTrials.gov page metadata did not match the request.');
	return { query: state.query, size: state.size, cursors: state.cursors, filters: state.filters, result };
};
