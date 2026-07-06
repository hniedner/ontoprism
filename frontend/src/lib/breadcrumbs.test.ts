import { describe, expect, it } from 'vitest';
import { buildBreadcrumbs } from './breadcrumbs';

describe('buildBreadcrumbs', () => {
	it('returns just Home for the root path', () => {
		expect(buildBreadcrumbs('/')).toEqual([{ label: 'Home', href: '/' }]);
	});

	it('maps known segments to friendly labels and accumulates hrefs', () => {
		expect(buildBreadcrumbs('/repositories/ncit')).toEqual([
			{ label: 'Home', href: '/' },
			{ label: 'Repositories', href: '/repositories' },
			{ label: 'NCIt Browser', href: '/repositories/ncit' }
		]);
	});

	it('shows an unknown (dynamic) segment verbatim, url-decoded', () => {
		const crumbs = buildBreadcrumbs('/repositories/ncit/C3262');
		expect(crumbs.at(-1)).toEqual({ label: 'C3262', href: '/repositories/ncit/C3262' });
	});

	it('decodes percent-encoded segments', () => {
		const crumbs = buildBreadcrumbs('/repositories/cadsr/CDE%20100');
		expect(crumbs.at(-1)?.label).toBe('CDE 100');
	});
});
