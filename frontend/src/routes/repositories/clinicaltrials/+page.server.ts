import { error } from '@sveltejs/kit';
import { searchClinicalTrials } from '#lib/api.clinicaltrials.ts';
import { gridControls } from '#lib/repository-registry.ts';
import { loadRemoteSearch } from '#lib/server/remote-search-load.ts';
import { parseCursorGridUrl } from '#lib/server/repository-load.ts';
import type { CTFilterPhase, CTStatus, CTStudySearchPage } from '#lib/types.ts';
import type { PageServerLoad } from './$types';

function sameValues(left: readonly string[], right: readonly string[] | undefined): boolean {
	return left.length === (right?.length ?? 0) && left.every((value, index) => value === right?.[index]);
}

export const load: PageServerLoad = async ({ fetch, url }) => {
	const filters = gridControls('clinicaltrials').filters;
	const state = parseCursorGridUrl(url, {
		filters: {
			status: filters.status as CTStatus[],
			phase: filters.phase as CTFilterPhase[]
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
