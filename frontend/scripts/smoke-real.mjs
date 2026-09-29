import { chromium } from '@playwright/test';

const base = process.argv[2];
if (!base?.startsWith('http://127.0.0.1:')) throw new Error('loopback frontend URL required');

const browser = await chromium.launch();
const errors = [];
const page = await browser.newPage();
page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
page.on('pageerror', (error) => errors.push(error.message));
page.on('response', (response) => { if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`); });

async function open(path) {
	const response = await page.goto(`${base}${path}`);
	if (!response || response.status() >= 500) throw new Error(`page failed: ${path} ${response?.status()}`);
}
async function rows(region) {
	const result = page.getByRole('region', { name: region }).locator('tbody tr');
	await result.first().waitFor();
	return result;
}
async function search(term, region) {
	await page.getByRole('searchbox').fill(term);
	await page.getByRole('button', { name: 'Search', exact: true }).click();
	await page.waitForURL((url) => url.searchParams.get('q') === term);
	return rows(region);
}
async function sort(name, value, expected = 'asc') {
	const region = page.locator('[role="region"][aria-label$="repository results"]');
	await page.getByRole('button', { name: `Sort by ${name}` }).click();
	await page.waitForURL((url) => url.searchParams.get('sort') === value);
	if (!(await page.locator('[aria-label="Active filters"]').textContent())?.includes('Sort:')) throw new Error('sort not rendered');
	const current = (await region.locator('tbody tr td:first-child').allTextContents()).map((text) => text.trim());
	if (current.length > 1 && expected === 'asc' && current.join('|') !== [...current].sort((a, b) => a.localeCompare(b, undefined, { numeric: true })).join('|')) throw new Error(`sort did not order ${name} rows`);
}
async function filter(column, option, parameter, value) {
	const region = page.locator('[role="region"][aria-label$="repository results"]');
	await page.getByRole('button', { name: `Filter ${column}`, exact: true }).click();
	await page.getByRole('dialog', { name: `${column} filter` }).getByRole('checkbox', { name: option, exact: true }).check();
	await page.waitForURL((url) => url.searchParams.getAll(parameter).includes(value));
	await region.locator('tbody tr').first().waitFor();
	const current = await region.locator('tbody tr').allTextContents();
	if (!current.length) throw new Error(`filter emptied ${column} rows`);
	const chip = page.getByRole('button', { name: `Clear ${column} filter` });
	await chip.waitFor();
	if (!(await chip.textContent())?.includes(option)) throw new Error(`filter did not apply ${option}`);
}

try {
	await open('/repositories/ncit');
	await rows('NCIt repository results');
	await search('melanoma', 'NCIt repository results');
	await sort('Code', 'code:asc');
	await filter('Status', 'Legacy pre-coordinated', 'representation_status', 'legacy-precoordinated');
	await page.getByRole('region', { name: 'NCIt repository results' }).locator('tbody a').first().click();
	await page.getByRole('heading', { name: 'Concept graph' }).waitFor();
	await page.getByRole('heading', { name: 'Additive provisional enhancement' }).waitFor();
	await page.getByRole('region', { name: 'Stated occurrence delta' }).waitFor();
	console.log('NCIt list/search/sort/filter/detail/graph/decomposition/delta: PASS');

	await open('/repositories/uberon');
	await rows('Uberon and Cell Ontology repository results');
	await search('lung', 'Uberon and Cell Ontology repository results');
	await sort('Code', 'code:asc');
	await filter('Source', 'Uberon', 'source', 'uberon');
	await page.getByRole('region', { name: 'Uberon and Cell Ontology repository results' }).locator('tbody a').first().click();
	await page.getByRole('heading').first().waitFor();
	console.log('Uberon list/search/sort/filter/detail: PASS');

	await open('/repositories/cadsr');
	await rows('caDSR CDE repository results');
	await search('tumor', 'caDSR CDE repository results');
	await sort('Public ID', 'public_id:asc');
	await page.getByRole('region', { name: 'caDSR CDE repository results' }).locator('tbody a').first().click();
	await page.getByRole('heading').first().waitFor();
	console.log('caDSR list/search/sort/detail: PASS; filter: not applicable (#487)');

	await open('/repositories/icdo/3.2/morphology');
	await rows('ICD-O repository results');
	await search('carcinoma', 'ICD-O repository results');
	await sort('Code', 'code:asc');
	await filter('Behaviour', '3', 'behaviour', '3');
	await page.getByRole('region', { name: 'ICD-O repository results' }).locator('tbody a').first().click();
	await page.getByRole('heading').first().waitFor();
	console.log('ICD-O list/search/code sort/behaviour filter/detail: PASS');

	await open('/repositories/pubmed');
	await search('melanoma', 'PubMed repository results');
	await sort('Date', 'pub_date', 'desc');
	await page.getByRole('region', { name: 'PubMed repository results' }).locator('tbody a').first().click();
	await page.getByRole('heading').first().waitFor();
	console.log('PubMed search/publication-date sort/detail: PASS; initial list and filter: not applicable (#487)');

	await open('/repositories/clinicaltrials');
	await search('melanoma', 'ClinicalTrials.gov repository results');
	await filter('Status', 'RECRUITING', 'status', 'RECRUITING');
	await page.getByRole('region', { name: 'ClinicalTrials.gov repository results' }).locator('tbody a').first().click();
	await page.getByRole('heading').first().waitFor();
	console.log('ClinicalTrials.gov search/status filter/detail: PASS; initial list and sort: not applicable (#487)');
	if (errors.length) throw new Error(`browser failures: ${errors.join('; ')}`);
} finally {
	await browser.close();
}
