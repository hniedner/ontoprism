import { expect, test, type Locator, type Page } from '@playwright/test';
import { gridCapabilities } from '../src/lib/repository-registry';

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
	const found = await rows(page, name);
	expect((await found.locator('td:nth-child(2)').allTextContents()).some((text) => text.toLowerCase().includes(term.toLowerCase()))).toBe(true);
	const absent = 'ontoprismnonexistenttermzqv834920';
	await page.getByRole('searchbox').fill(absent);
	await page.getByRole('button', { name: 'Search', exact: true }).click();
	await expect(page).toHaveURL((url) => url.searchParams.get('q') === absent);
	await expect(region(page, name)).toHaveAttribute('aria-busy', 'false');
	await expect(region(page, name).locator('tbody tr')).toHaveCount(1);
	await expect(region(page, name).locator('tbody tr td')).toHaveAttribute('colspan', /[1-9]/);
	await page.getByRole('searchbox').fill(term);
	await page.getByRole('button', { name: 'Search', exact: true }).click();
	await expect(page).toHaveURL((url) => url.searchParams.get('q') === term);
	await rows(page, name);
	return found;
}

async function detail(page: Page, name: string, identifier: string): Promise<void> {
	const link = region(page, name).locator('tbody a').first();
	const path = await link.getAttribute('href');
	expect(path).toBeTruthy();
	await link.click();
	await expect(page).toHaveURL((url) => url.pathname === path);
	await expect(page.locator('main').getByText(identifier, { exact: false }).first()).toBeVisible();
}

async function sort(page: Page, regionName: string, name: string, value: string, kind: 'text' | 'numeric' | 'date' | 'label' = 'text'): Promise<void> {
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
			: kind === 'label' ? (a: string, b: string) => a.localeCompare(b, 'en', { ignorePunctuation: true })
			: (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;
		expect(values).toEqual([...values].sort(comparator));
	}
}

async function filter(page: Page, regionName: string, column: string, option: string, parameter: string, value: string, rendered: string, displayMatchesSelection = true): Promise<void> {
	const table = region(page, regionName);
	const header = table.locator('th').filter({ has: page.getByRole('button', { name: `Filter ${column}`, exact: true }) });
	const index = await header.evaluate((element) => Array.from(element.parentElement!.children).indexOf(element));
	await header.getByRole('button', { name: `Filter ${column}`, exact: true }).click();
	await page.getByRole('dialog', { name: `${column} filter` }).getByRole('checkbox', { name: option, exact: true }).check();
	await expect(page).toHaveURL((url) => url.searchParams.getAll(parameter).includes(value));
	await rows(page, regionName);
	const cells = table.locator(`tbody tr td:nth-child(${index + 1})`);
	expect(await cells.count()).toBeGreaterThan(0);
	if (displayMatchesSelection) for (const text of await cells.allTextContents()) expect(text.trim()).toBe(rendered);
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
	const passed = (message: string) => {
		expect(errors).toEqual([]);
		console.log(message);
	};
	await open('/repositories/ncit');
	await rows(page, 'NCIt repository results');
	await search(page, 'melanoma', 'NCIt repository results');
	const ncit = gridCapabilities('ncit');
	const table = region(page, 'NCIt repository results');
	for (const sortValue of ncit.sorts.search.filter((value) => value.endsWith(':asc'))) {
		const column = sortValue.split(':')[0];
		const header = table.locator(`th[data-column-id="${column}"]`);
		const name = (await header.getByRole('button', { name: /^Sort by / }).getAttribute('aria-label'))!.replace(/^Sort by /, '');
		await sort(page, 'NCIt repository results', name, sortValue, column === 'label' ? 'label' : 'text');
	}
	await sort(page, 'NCIt repository results', 'Code', ncit.sorts.search.find((value) => value === 'code:asc')!);
	for (const [column, control] of Object.entries(ncit.filters).sort(([, a], [, b]) => Number(b.kind === 'categorical') - Number(a.kind === 'categorical'))) {
		const header = table.locator(`th[data-column-id="${column}"]`);
		const button = header.getByRole('button', { name: /^Filter / });
		const label = (await button.getAttribute('aria-label'))!.replace(/^Filter /, '');
		const columnIndex = await header.evaluate((element) => Array.from(element.parentElement!.children).indexOf(element));
		const current = (await table.locator('tbody tr').first().locator('td').nth(columnIndex).textContent())!.trim();
		let textValue = current;
		if (control.kind === 'categorical') {
			const [value, rendered] = control.source_domain ? [current, current] : Object.entries(control.values)[0];
			await filter(page, 'NCIt repository results', label, rendered, column, value, rendered, !control.multiple);
			textValue = rendered;
			await page.keyboard.press('Escape');
		}
		await button.click();
		const input = page.getByRole('textbox', { name: `Filter ${label} text` });
		await input.fill(textValue);
		await input.press('Enter');
		await expect(page).toHaveURL((url) => url.searchParams.get(`text_${column}`) === textValue);
		await rows(page, 'NCIt repository results');
		await input.press('Escape');
	}
	const ncitCode = (await table.locator('tbody a').first().textContent())!.trim();
	await detail(page, 'NCIt repository results', ncitCode);
	await expect(page.getByRole('heading', { name: 'Concept graph' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Additive provisional enhancement' })).toBeVisible();
	await expect(region(page, 'Stated occurrence delta')).toBeVisible();
	passed('NCIt list/search/sort/filter/detail/graph/decomposition/delta: PASS');

	await open('/repositories/uberon');
	await rows(page, 'Uberon and Cell Ontology repository results');
	await search(page, 'lung', 'Uberon and Cell Ontology repository results');
	await sort(page, 'Uberon and Cell Ontology repository results', 'Code', 'code:asc');
	await filter(page, 'Uberon and Cell Ontology repository results', 'Source', 'Uberon', 'source', 'uberon', 'Uberon');
	await detail(page, 'Uberon and Cell Ontology repository results', 'UBERON:');
	passed('Uberon list/search/sort/filter/detail: PASS');

	await open('/repositories/cadsr');
	await rows(page, 'caDSR CDE repository results');
	await search(page, 'tumor', 'caDSR CDE repository results');
	await sort(page, 'caDSR CDE repository results', 'Public ID', 'public_id:asc', 'numeric');
	const cde = await region(page, 'caDSR CDE repository results').locator('tbody a').first().textContent();
	await detail(page, 'caDSR CDE repository results', cde!.trim());
	await expect(page.getByRole('heading', { name: 'Concept graph' })).toBeVisible();
	await page.getByRole('button', { name: 'Explore in graph' }).click();
	await expect(page.getByRole('heading', { name: 'Network' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Export as PNG' })).toBeVisible();
	passed('caDSR list/search/sort/detail/graph: PASS; filter: not applicable (#487)');

	await open('/repositories/icdo/3.2/morphology');
	await rows(page, 'ICD-O repository results');
	await search(page, 'carcinoma', 'ICD-O repository results');
	await sort(page, 'ICD-O repository results', 'Code', 'code:asc');
	await filter(page, 'ICD-O repository results', 'Behaviour', '3', 'behaviour', '3', '3');
	const icdo = await region(page, 'ICD-O repository results').locator('tbody a').first().textContent();
	await detail(page, 'ICD-O repository results', icdo!.trim());
	passed('ICD-O list/search/code sort/behaviour filter/detail: PASS');

	await open('/repositories/pubmed');
	await search(page, 'melanoma', 'PubMed repository results');
	await sort(page, 'PubMed repository results', 'Date', 'pub_date', 'date');
	const pmid = await region(page, 'PubMed repository results').locator('tbody a').first().textContent();
	await detail(page, 'PubMed repository results', pmid!.trim());
	passed('PubMed search/publication-date sort/detail: PASS; initial list and filter: not applicable (#487)');

	await open('/repositories/clinicaltrials');
	await search(page, 'melanoma', 'ClinicalTrials.gov repository results');
	await filter(page, 'ClinicalTrials.gov repository results', 'Status', 'RECRUITING', 'status', 'RECRUITING', 'RECRUITING');
	const nct = await region(page, 'ClinicalTrials.gov repository results').locator('tbody a').first().textContent();
	await detail(page, 'ClinicalTrials.gov repository results', nct!.trim());
	passed('ClinicalTrials.gov search/status filter/detail: PASS; initial list and sort: not applicable (#487)');
});
