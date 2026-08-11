import { describe, expect, it } from 'vitest';
import { buildBreadcrumbs } from './breadcrumbs';

const DYNAMIC_SEGMENT_EXAMPLES: Readonly<Record<string, string>> = {
	code: 'C27262',
	id: '6686721',
	nct: 'NCT00000001',
	pmid: '12345678'
};

function concretePagePath(pageModulePath: string): string {
	const routePath = pageModulePath
		.replace('../routes', '')
		.replace('/+page.svelte', '')
		.replaceAll(/\[([^\]]+)\]/g, (_match, parameter: string) => {
			const example = DYNAMIC_SEGMENT_EXAMPLES[parameter];
			if (example === undefined) {
				throw new Error(`Missing route example for [${parameter}]`);
			}
			return example;
		});
	return routePath || '/';
}

describe('buildBreadcrumbs', () => {
	it('returns just Home for the root path', () => {
		expect(buildBreadcrumbs('/')).toEqual([{ label: 'Home', href: '/' }]);
	});

	it('omits the layout-only repositories segment from list and detail trails', () => {
		expect(buildBreadcrumbs('/repositories/ncit')).toEqual([
			{ label: 'Home', href: '/' },
			{ label: 'NCIt Browser', href: '/repositories/ncit' }
		]);
		expect(buildBreadcrumbs('/repositories/ncit/C27262')).toEqual([
			{ label: 'Home', href: '/' },
			{ label: 'NCIt Browser', href: '/repositories/ncit' },
			{ label: 'C27262', href: '/repositories/ncit/C27262' }
		]);
	});

	it('emits only hrefs backed by real SvelteKit pages for every repository route', () => {
		const pageModules = import.meta.glob('../routes/**/+page.svelte');
		const pagePaths = new Set(Object.keys(pageModules).map(concretePagePath));
		const repositoryPaths = [...pagePaths].filter((path) => path.startsWith('/repositories/'));

		expect(repositoryPaths).toHaveLength(8);
		for (const routePath of repositoryPaths) {
			for (const crumb of buildBreadcrumbs(routePath)) {
				expect(pagePaths, `${routePath} generated missing page ${crumb.href}`).toContain(crumb.href);
			}
		}
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
