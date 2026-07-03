import { describe, expect, it } from 'vitest';
import { apiUrl } from './api';

describe('apiUrl', () => {
	it('returns the bare path when there are no params', () => {
		expect(apiUrl('/api/v1/ncit/concepts/C3262')).toBe('/api/v1/ncit/concepts/C3262');
	});

	it('appends and encodes query params', () => {
		expect(apiUrl('/api/v1/ncit/search', { q: 'small cell', limit: 10 })).toBe(
			'/api/v1/ncit/search?q=small+cell&limit=10'
		);
	});
});
