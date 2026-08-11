import type { RepresentationStatus } from '$lib/types';

const LEGACY_PRECOORDINATED: RepresentationStatus = 'legacy-precoordinated';

export function parseRepresentationStatus(
	params: URLSearchParams
): RepresentationStatus | null {
	return params.get('representation_status') === LEGACY_PRECOORDINATED
		? LEGACY_PRECOORDINATED
		: null;
}

export function updateRepresentationStatusSearch(
	current: URLSearchParams,
	status: RepresentationStatus | null
): URLSearchParams {
	const params = new URLSearchParams(current);
	if (status) params.set('representation_status', status);
	else params.delete('representation_status');
	params.delete('offset');
	return params;
}
