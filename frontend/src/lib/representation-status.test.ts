import { describe, expect, it } from 'vitest';
import {
	parseRepresentationStatus,
	updateRepresentationStatusSearch
} from './representation-status';

describe('representation-status URL state', () => {
	it('accepts only the published legacy marker', () => {
		expect(
			parseRepresentationStatus(
				new URLSearchParams('representation_status=legacy-precoordinated')
			)
		).toBe('legacy-precoordinated');
		expect(parseRepresentationStatus(new URLSearchParams('representation_status=atomic'))).toBeNull();
		expect(parseRepresentationStatus(new URLSearchParams())).toBeNull();
	});

	it('preserves the query and resets pagination when the facet changes', () => {
		expect(
			updateRepresentationStatusSearch(
				new URLSearchParams('q=neo&offset=50'),
				'legacy-precoordinated'
			).toString()
		).toBe('q=neo&representation_status=legacy-precoordinated');
		expect(
			updateRepresentationStatusSearch(
				new URLSearchParams('q=neo&representation_status=legacy-precoordinated'),
				null
			).toString()
		).toBe('q=neo');
	});
});
