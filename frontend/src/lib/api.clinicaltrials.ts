// ClinicalTrials.gov API client. Kept separate from the core NCIt/caDSR api module and
// built on its shared request helpers (apiUrl / getJson / postJsonBody).

import { apiUrl, getJson, postJsonBody } from './api';
import type { CTSearchRequest, CTStudyDetail, CTStudySearchPage } from './types';

/** Search ClinicalTrials.gov by condition / intervention / free term (+ filters). */
export function searchClinicalTrials(
	request: CTSearchRequest,
	fetchImpl?: typeof fetch
): Promise<CTStudySearchPage> {
	return postJsonBody<CTStudySearchPage>(
		apiUrl('/api/v1/clinicaltrials/search'),
		request,
		fetchImpl
	);
}

/** Fetch one trial's full detail by NCT id. */
export function getTrial(nctId: string, fetchImpl?: typeof fetch): Promise<CTStudyDetail> {
	return getJson<CTStudyDetail>(
		apiUrl(`/api/v1/clinicaltrials/${encodeURIComponent(nctId)}`),
		fetchImpl
	);
}
