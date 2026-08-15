import { expect, test } from '@playwright/test';

test('every repository breadcrumb link resolves and the final crumb remains inert', async ({
	page
}) => {
	const repositoryRoutes = [
		'/repositories/ncit',
		'/repositories/ncit/C27262',
		'/repositories/uberon',
		'/repositories/uberon/UBERON:0002048',
		'/repositories/icdo',
		'/repositories/cadsr',
		'/repositories/cadsr/2001',
		'/repositories/clinicaltrials',
		'/repositories/clinicaltrials/NCT01234567',
		'/repositories/pubmed',
		'/repositories/pubmed/12345678'
	];

	for (const route of repositoryRoutes) {
		const response = await page.goto(route);
		expect(response?.status(), route).toBe(200);

		const breadcrumbs = page.getByRole('navigation', { name: 'Breadcrumb' });
		const links = breadcrumbs.getByRole('link');
		for (const link of await links.all()) {
			const href = await link.getAttribute('href');
			expect(href, `${route} breadcrumb link has no href`).not.toBeNull();
			expect(href, `${route} exposes the layout-only repositories path`).not.toBe('/repositories');
			const target = await page.request.get(href!);
			expect(target.status(), `${route} breadcrumb ${href}`).toBeLessThan(400);
		}

		const finalCrumb = breadcrumbs.locator('span.font-medium');
		await expect(finalCrumb).toHaveCount(1);
		expect(await finalCrumb.evaluate((element) => element.tagName)).toBe('SPAN');
		expect(await finalCrumb.getAttribute('href')).toBeNull();
	}
});

test('ICD-O entitlement is server-side, no-leak, and slash codes use one safe segment', async ({ page, request }) => {
	const refused = await request.get('http://127.0.0.1:4174/repositories/icdo/3.2/morphology', {
		headers: { 'X-ICDO-Entitlement': 'licensed' }
	});
	expect(refused.status()).toBe(403);
	const refusedHtml = await refused.text();
	expect(refusedHtml).not.toContain('Protected intraductal papilloma');
	expect(refusedHtml).not.toContain('Protected papilloma synonym');

	const list = await page.goto('/repositories/icdo/3.2/morphology');
	expect(list?.status()).toBe(200);
	expect(await list?.text()).toContain('Protected ICD-O-3.2 morphology');
	await page.getByRole('link', { name: '8503/0' }).click();
	await expect(page).toHaveURL('/repositories/icdo/3.2/morphology/ODUwMy8w');
	expect(new URL(page.url()).pathname.split('/').at(-1)).toBe('ODUwMy8w');
	await expect(page.getByText('Protected papilloma synonym')).toBeVisible();
	await page.reload();
	await expect(page.getByRole('heading', { name: '8503/0' })).toBeVisible();
	expect(await page.context().cookies()).toEqual([]);
	const browserStorage = await page.evaluate(() =>
		JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } })
	);
	expect(browserStorage).not.toContain('licensed');
	expect(browserStorage).not.toContain('icdo_entitlement');
	expect(page.url()).not.toContain('licensed');
	expect(await list?.text()).not.toContain('licensed');
	const javascriptUrls = await page.evaluate(() =>
		performance
			.getEntriesByType('resource')
			.map((entry) => entry.name)
			.filter((url) => url.endsWith('.js'))
	);
	expect(javascriptUrls.length).toBeGreaterThan(0);
	for (const url of javascriptUrls) {
		expect(await (await request.get(url)).text()).not.toContain('licensed');
	}
});

test('P334 reciprocal alignment links are accessible in both entitled directions', async ({ page }) => {
	const ncit = await page.goto('/repositories/ncit/C188218');
	expect(ncit?.status()).toBe(200);
	for (const [code, segment] of [['8240/3', 'ODI0MC8z'], ['8241/3', 'ODI0MS8z'], ['8248/1', 'ODI0OC8x']] as const) {
		await expect(page.getByRole('link', { name: `Open aligned ICD-O-3.2 morphology code ${code}` })).toHaveAttribute(
			'href',
			`/repositories/icdo/3.2/morphology/${segment}`
		);
	}

	await page.goto('/repositories/icdo/3.2/morphology/ODUwMy8w');
	for (const code of ['C45194', 'C71720', 'C80281', 'C80289', 'C80291', 'C8851', 'C9496']) {
		await expect(page.getByRole('link', { name: `Open aligned NCIt concept ${code}` })).toBeVisible();
	}
});

test('protected congruence report is present in entitled initial HTML only', async ({ request }) => {
	const refused = await request.get('http://127.0.0.1:4174/repositories/icdo/4.0/topography/congruence');
	expect(refused.status()).toBe(403);
	expect(await refused.text()).not.toContain('C34.9');
	const response = await request.get('/repositories/icdo/4.0/topography/congruence');
	expect(response.status()).toBe(200);
	const html = await response.text();
	expect(html).toContain('All 406 ICD-O-4 topography codes are classified once.');
	expect(html).toContain('C34.9');
	expect(html).not.toContain('exactMatch');
});

test('all ICD-O datasets support search, pagination, detail, and explicit access status', async ({ page, request }) => {
	for (const [edition, axis, code] of [
		['3.2', 'morphology', '8503/0'],
		['4.0', 'morphology', '8240/3'],
		['4.0', 'topography', 'C34.9']
	] as const) {
		await page.goto(`/repositories/icdo/${edition}/${axis}?q=protected`);
		await expect(page.getByRole('heading', { name: `ICD-O-${edition} ${axis}` })).toBeVisible();
		await page.getByRole('button', { name: 'Next page' }).click();
		await expect(page).toHaveURL(new RegExp(`q=protected&offset=25$`));
		await page.getByRole('link', { name: code }).click();
		await expect(page.getByRole('heading', { name: code })).toBeVisible();
	}

	await page.goto('/repositories/icdo');
	await expect(page.getByLabel('Ready and entitled').first()).toBeVisible();
	const refused = await request.get('http://127.0.0.1:4174/repositories/icdo');
	expect(await refused.text()).toContain('Entitlement required');
});

test('browser-side decomposition receives entitled ICD-O mappings through the BFF', async ({ request }) => {
	const response = await request.get('/api/v1/ncit/concepts/C3262/decomposition');
	expect(response.status()).toBe(200);
	const body = await response.json();
	expect(body.constituents[0].upstream[0].object_id).toBe('8503/0');
});

test('built adapter-node SSR includes NCIt browse data and hydration does not fetch it twice', async ({
	page
}) => {
	const offset = 1_000 + Math.floor(Math.random() * 1_000_000);
	const response = await page.goto(`/repositories/ncit?offset=${offset}`);

	expect(response?.status()).toBe(200);
	expect(await response?.text()).toContain('SSR Neoplasm');
	await expect(page.getByRole('link', { name: 'SSR Neoplasm' })).toBeVisible();

	const countsResponse = await page.request.get('/api/v1/__test__/counts');
	expect(countsResponse.status()).toBe(200);
	const counts = (await countsResponse.json()) as Record<string, number>;
	expect(counts[`GET /api/v1/ncit/list?limit=25&offset=${offset}`]).toBe(1);
});

test('NCIt search and pagination are URL state rerun through the server load', async ({ page }) => {
	await page.goto('/repositories/ncit');
	await page.getByRole('searchbox').fill('melanoma');
	await page.getByRole('button', { name: 'Search' }).click();

	await expect(page).toHaveURL('/repositories/ncit?q=melanoma');
	await expect(page.getByRole('link', { name: 'SSR result for melanoma' })).toBeVisible();
	await page.getByRole('button', { name: 'Next page' }).click();
	await expect(page).toHaveURL('/repositories/ncit?q=melanoma&offset=25');
	await expect(page.getByText('Page 2 of 3')).toBeVisible();
	await page.reload();
	await expect(page.getByRole('searchbox')).toHaveValue('melanoma');
	await expect(page).toHaveURL('/repositories/ncit?q=melanoma&offset=25');
	await expect(page.getByRole('link', { name: 'SSR result for melanoma' })).toBeVisible();
});

test('BFF preserves upstream failures and bounds slow FastAPI requests', async ({ request }) => {
	for (const status of [404, 503]) {
		const response = await request.get(`/api/v1/__test__/status/${status}`);
		expect(response.status()).toBe(status);
		expect(response.headers()['x-upstream-contract']).toBe('preserved');
		expect(await response.json()).toEqual({ detail: `upstream ${status}` });
	}

	const timeout = await request.get('/api/v1/__test__/slow');
	expect(timeout.status()).toBe(504);
	expect(await timeout.json()).toEqual({ detail: 'FastAPI request timed out' });

	const stalledBody = await request.get('/api/v1/__test__/stalled-body');
	expect(stalledBody.status()).toBe(504);
	expect(await stalledBody.json()).toEqual({ detail: 'FastAPI request timed out' });

	const redirect = await request.get('/api/v1/__test__/redirect', { maxRedirects: 0 });
	expect(redirect.status()).toBe(307);
	expect(redirect.headers()['location']).toBeUndefined();

	const headers = await request.get('/api/v1/__test__/forwarding-headers', {
		headers: {
			forwarded: 'for=198.51.100.1',
			'x-forwarded-for': '198.51.100.1',
			'x-real-ip': '198.51.100.1'
		}
	});
	const receivedHeaders = await headers.json();
	expect(receivedHeaders.forwarded).toBeNull();
	expect(receivedHeaders.x_real_ip).toBeNull();
	expect(receivedHeaders.x_forwarded_for).not.toBe('198.51.100.1');
	expect(receivedHeaders.x_forwarded_for).toMatch(/127\.0\.0\.1|::1/);
});

test('caDSR list and detail critical data are present in initial HTML', async ({ request }) => {
	const list = await request.get('/repositories/cadsr');
	expect(list.status()).toBe(200);
	expect(await list.text()).toContain('Tumor Stage Code');

	const detail = await request.get('/repositories/cadsr/2001');
	expect(detail.status()).toBe(200);
	expect(await detail.text()).toContain('The stage of a tumor.');
});

test('NCIt detail and graph placeholder SSR once before browser-only graph hydration', async ({ page }) => {
	const code = `CSSR${Math.floor(Math.random() * 1_000_000)}`;
	const response = await page.goto(`/repositories/ncit/${code}`);
	expect(response?.status()).toBe(200);
	const html = await response?.text();
	expect(html).toContain('SSR concept definition from FastAPI.');
	expect(html).toContain('Loading concept graph');
	const graphRegion = page.locator('.graph-canvas').locator('..');
	await expect(graphRegion).toBeVisible();
	expect((await graphRegion.boundingBox())?.height).toBeGreaterThanOrEqual(500);

	const countsResponse = await page.request.get('/api/v1/__test__/counts');
	const counts = (await countsResponse.json()) as Record<string, number>;
	expect(counts[`GET /api/v1/ncit/concepts/${code}`]).toBe(1);
	expect(counts[`GET /api/v1/ncit/concepts/${code}/neighborhood?depth=1`]).toBe(1);
});

test('Uberon list and detail critical data are present in initial HTML', async ({ request }) => {
	const list = await request.get('/repositories/uberon');
	expect(list.status()).toBe(200);
	expect(await list.text()).toContain('SSR lung');

	const detail = await request.get('/repositories/uberon/UBERON:0002048');
	expect(detail.status()).toBe(200);
	expect(await detail.text()).toContain('SSR Uberon concept definition from FastAPI.');
});

test('ClinicalTrials and PubMed search URLs and detail routes render initial content', async ({
	request
}) => {
	const trialsEmpty = await request.get('/repositories/clinicaltrials');
	expect(await trialsEmpty.text()).toContain('Enter a condition above to search');
	const trialsSearch = await request.get('/repositories/clinicaltrials?q=melanoma');
	expect(await trialsSearch.text()).toContain('A Study of Widgetinib');
	const trial = await request.get('/repositories/clinicaltrials/NCT01234567');
	expect(await trial.text()).toContain('A Phase 2 Study of Widgetinib in Melanoma');

	const pubmedEmpty = await request.get('/repositories/pubmed');
	expect(await pubmedEmpty.text()).toContain('Enter a query above to search');
	const pubmedSearch = await request.get('/repositories/pubmed?q=immunotherapy');
	expect(await pubmedSearch.text()).toContain('SSR article for immunotherapy');
	const article = await request.get('/repositories/pubmed/12345678');
	expect(await article.text()).toContain('SSR abstract from FastAPI.');
});

test('repository kind is persistent on navigation, list, and detail surfaces', async ({ page }) => {
	await page.goto('/repositories/ncit');
	await expect(page.getByRole('navigation').getByText('Local', { exact: true }).first()).toBeVisible();
	await expect(page.getByText('Local certified proxy', { exact: true })).toBeVisible();

	await page.goto('/repositories/pubmed/12345678');
	await expect(page.getByText('Remote live service', { exact: true })).toBeVisible();
});

test('remote search discloses live queries and renders typed failures without identity fields', async ({
	page
}) => {
	for (const [repository, query, state, message] of [
		['pubmed', 'rate-limit-private-query', 'rate-limited', 'PubMed rate limit reached'],
		['pubmed', 'timeout-private-query', 'timeout', 'PubMed request timed out'],
		['pubmed', 'unavailable-private-query', 'unavailable', 'PubMed is temporarily unavailable'],
		['clinicaltrials', 'rate-limit-private-query', 'rate-limited', 'ClinicalTrials.gov rate limit reached'],
		['clinicaltrials', 'timeout-private-query', 'timeout', 'ClinicalTrials.gov request timed out'],
		['clinicaltrials', 'unavailable-private-query', 'unavailable', 'ClinicalTrials.gov is temporarily unavailable']
	] as const) {
		await page.goto(`/repositories/${repository}?q=${query}`);
		await expect(page.getByText(message, { exact: false })).toBeVisible();
		await expect(page.locator(`[data-remote-state="${state}"]`)).toBeVisible();
		await expect(page.getByText(query, { exact: false })).toHaveCount(0);
		await expect(page.getByText('Release', { exact: true })).toHaveCount(0);
		await expect(page.getByText('Source identity', { exact: true })).toHaveCount(0);
	}

	await page.goto('/repositories/pubmed');
	await expect(page.getByRole('note').getByText(/NCBI PubMed is queried live/)).toBeVisible();
	await page.goto('/repositories/pubmed/12345678');
	await expect(page.getByRole('note').getByText(/not reproducible from certified local state/)).toBeVisible();
	await page.goto('/repositories/clinicaltrials');
	await expect(page.getByRole('note').getByText(/ClinicalTrials.gov is queried live/)).toBeVisible();
	await page.goto('/repositories/clinicaltrials/NCT01234567');
	await expect(page.getByRole('note')).toContainText('Query and request data are sent to ClinicalTrials.gov');
});

test('refresh is explicitly local-only and its slow mutation uses the shared delayed status', async ({ page }) => {
	const response = await page.goto('/refresh');
	expect(await response?.text()).toContain('Re-certify the active NCIt, caDSR, Uberon/CL, and ICD-O local repositories');
	expect(await response?.text()).toContain('Remote live services are not refreshed');
	expect(page.getByRole('status')).not.toBeVisible();

	await page.getByRole('button', { name: 'Refresh repositories' }).click();
	await expect(page.getByRole('status')).toHaveText('Refreshing repositories');
	await expect(page.getByText('fixture metadata')).toBeVisible();
	await expect(page.getByRole('status')).not.toBeVisible();
});

test('route-critical 404, 503, and timeout responses retain explicit HTTP status', async ({
	request
}) => {
	for (const [code, status, message] of [
		['C404', 404, 'concept upstream 404'],
		['C503', 503, 'concept upstream 503'],
		['CTIMEOUT', 504, 'FastAPI request timed out']
	] as const) {
		const response = await request.get(`/repositories/ncit/${code}`);
		expect(response.status()).toBe(status);
		expect(await response.text()).toContain(message);
	}
});

test('slow client navigation exposes the shared delayed critical-load status', async ({ page }) => {
	await page.goto('/repositories/ncit?q=slow');
	const navigation = page.getByRole('link', { name: 'SSR result for slow' }).click();
	const loadingPage = page.getByRole('status').filter({ hasText: 'Loading page' });
	await expect(loadingPage).toBeVisible();
	await navigation;
	await expect(page).toHaveURL('/repositories/ncit/CSLOW');
	await expect(page.getByText('SSR concept definition from FastAPI.')).toBeVisible();
	await expect(loadingPage).not.toBeVisible();
});
