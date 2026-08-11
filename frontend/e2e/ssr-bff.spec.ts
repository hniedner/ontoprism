import { expect, test } from '@playwright/test';

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
	expect(await headers.json()).toEqual({ forwarded: null, x_forwarded_for: null, x_real_ip: null });
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

test('refresh structure is SSR and its slow mutation uses the shared delayed status', async ({ page }) => {
	const response = await page.goto('/refresh');
	expect(await response?.text()).toContain('Re-certify the active NCIt and caDSR proxies');
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
