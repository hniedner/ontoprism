import { expect, test, type Locator, type Page } from '@playwright/test';

const region = (page: Page, name: string) => page.getByRole('region', { name, exact: true });

async function rows(page: Page, name: string): Promise<Locator> {
	const table = region(page, name);
	await expect(table).toHaveAttribute('aria-busy', 'false');
	const result = table.locator('tbody tr');
	await expect(result.first()).toBeVisible();
	await expect(result.first().locator('td')).toHaveCount(await table.locator('thead th').count());
	return result;
}

async function search(page: Page, term: string, name: string): Promise<Locator> {
	await page.getByRole('searchbox').fill(term);
	await page.getByRole('button', { name: 'Search', exact: true }).click();
	await expect(page).toHaveURL((url) => url.searchParams.get('q') === term);
	return rows(page, name);
}

async function sort(page: Page, regionName: string, name: string, value: string, kind: 'text' | 'numeric' | 'date' = 'text'): Promise<void> {
	const table = region(page, regionName);
	const header = table.locator('th').filter({ has: page.getByRole('button', { name: `Sort by ${name}` }) });
	const column = await header.evaluate((element) => Array.from(element.parentElement!.children).indexOf(element));
	await header.getByRole('button', { name: `Sort by ${name}` }).click();
	await expect(page).toHaveURL((url) => url.searchParams.get('sort') === value);
	await expect(header).toHaveAttribute('aria-sort', kind === 'date' ? 'descending' : 'ascending');
	await rows(page, regionName);
	const values = (await table.locator(`tbody tr td:nth-child(${column + 1})`).allTextContents()).map((text) => text.trim());
	expect(values.length).toBeGreaterThan(1);
	if (kind === 'date') {
		// PubMed may return partial dates or seasons; the year is consistently present.
		const years = values.map((value) => Number(value.match(/\b(?:19|20)\d{2}\b/)?.[0]));
		expect(years.every(Number.isFinite)).toBe(true);
		expect(years).toEqual([...years].sort((a, b) => b - a));
	} else {
		const comparator = kind === 'numeric'
			? (a: string, b: string) => Number.parseInt(a, 10) - Number.parseInt(b, 10)
			: (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;
		expect(values).toEqual([...values].sort(comparator));
	}
}

async function filter(page: Page, regionName: string, column: string, option: string, parameter: string, value: string, rendered: string): Promise<void> {
	const table = region(page, regionName);
	const header = table.locator('th').filter({ has: page.getByRole('button', { name: `Filter ${column}`, exact: true }) });
	const index = await header.evaluate((element) => Array.from(element.parentElement!.children).indexOf(element));
	await header.getByRole('button', { name: `Filter ${column}`, exact: true }).click();
	await page.getByRole('dialog', { name: `${column} filter` }).getByRole('checkbox', { name: option, exact: true }).check();
	await expect(page).toHaveURL((url) => url.searchParams.getAll(parameter).includes(value));
	await rows(page, regionName);
	const cells = table.locator(`tbody tr td:nth-child(${index + 1})`);
	expect(await cells.count()).toBeGreaterThan(0);
	for (const text of await cells.allTextContents()) expect(text.trim()).toBe(rendered);
}

test('read-only configured repository smoke', async ({ page }) => {
	const errors: string[] = [];
	page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
	page.on('pageerror', (error) => errors.push(error.message));
	page.on('response', (response) => { if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`); });
	const open = async (path: string) => {
		const response = await page.goto(path);
		expect(response?.status()).toBeLessThan(500);
	};
	await open('/repositories/ncit');
	await rows(page, 'NCIt repository results');
	await search(page, 'melanoma', 'NCIt repository results');
	await sort(page, 'NCIt repository results', 'Code', 'code:asc');
	await filter(page, 'NCIt repository results', 'Status', 'Legacy pre-coordinated', 'representation_status', 'legacy-precoordinated', 'Legacy pre-coordinated');
	await region(page, 'NCIt repository results').locator('tbody a').first().click();
	await expect(page.getByRole('heading', { name: 'Concept graph' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Additive provisional enhancement' })).toBeVisible();
	await expect(region(page, 'Stated occurrence delta')).toBeVisible();
	console.log('NCIt list/search/sort/filter/detail/graph/decomposition/delta: PASS');

	await open('/repositories/uberon');
	await rows(page, 'Uberon and Cell Ontology repository results');
	await search(page, 'lung', 'Uberon and Cell Ontology repository results');
	await sort(page, 'Uberon and Cell Ontology repository results', 'Code', 'code:asc');
	await filter(page, 'Uberon and Cell Ontology repository results', 'Source', 'Uberon', 'source', 'uberon', 'Uberon');
	await region(page, 'Uberon and Cell Ontology repository results').locator('tbody a').first().click();
	await expect(page.getByRole('heading').first()).toBeVisible();
	console.log('Uberon list/search/sort/filter/detail: PASS');

	await open('/repositories/cadsr');
	await rows(page, 'caDSR CDE repository results');
	await search(page, 'tumor', 'caDSR CDE repository results');
	await sort(page, 'caDSR CDE repository results', 'Public ID', 'public_id:asc', 'numeric');
	await region(page, 'caDSR CDE repository results').locator('tbody a').first().click();
	await expect(page.getByRole('heading').first()).toBeVisible();
	console.log('caDSR list/search/sort/detail: PASS; filter: not applicable (#487)');

	await open('/repositories/icdo/3.2/morphology');
	await rows(page, 'ICD-O repository results');
	await search(page, 'carcinoma', 'ICD-O repository results');
	await sort(page, 'ICD-O repository results', 'Code', 'code:asc');
	await filter(page, 'ICD-O repository results', 'Behaviour', '3', 'behaviour', '3', '3');
	await region(page, 'ICD-O repository results').locator('tbody a').first().click();
	await expect(page.getByRole('heading').first()).toBeVisible();
	console.log('ICD-O list/search/code sort/behaviour filter/detail: PASS');

	await open('/repositories/pubmed');
	await search(page, 'melanoma', 'PubMed repository results');
	await sort(page, 'PubMed repository results', 'Date', 'pub_date', 'date');
	await region(page, 'PubMed repository results').locator('tbody a').first().click();
	await expect(page.getByRole('heading').first()).toBeVisible();
	console.log('PubMed search/publication-date sort/detail: PASS; initial list and filter: not applicable (#487)');

	await open('/repositories/clinicaltrials');
	await search(page, 'melanoma', 'ClinicalTrials.gov repository results');
	await filter(page, 'ClinicalTrials.gov repository results', 'Status', 'RECRUITING', 'status', 'RECRUITING', 'RECRUITING');
	await region(page, 'ClinicalTrials.gov repository results').locator('tbody a').first().click();
	await expect(page.getByRole('heading').first()).toBeVisible();
	console.log('ClinicalTrials.gov search/status filter/detail: PASS; initial list and sort: not applicable (#487)');
	expect(errors).toEqual([]);
});
