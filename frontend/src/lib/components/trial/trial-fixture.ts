import type { CTStudyDetail } from '$lib/types';

/** Build a CTStudyDetail with sensible empty defaults, overridable per test. */
export function trialDetail(over: Partial<CTStudyDetail> = {}): CTStudyDetail {
	return {
		nct_id: 'NCT01',
		title: 'A Trial',
		official_title: null,
		status: null,
		phase: null,
		study_type: null,
		primary_purpose: null,
		conditions: [],
		interventions: [],
		primary_outcomes: [],
		secondary_outcomes: [],
		eligibility_criteria: null,
		enrollment: null,
		start_date: null,
		sponsors: [],
		locations: [],
		references: [],
		url: 'https://clinicaltrials.gov/study/NCT01',
		...over
	};
}
