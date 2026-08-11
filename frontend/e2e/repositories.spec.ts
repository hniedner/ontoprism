import { expect, test } from '@playwright/test';

test('caDSR: browse → URL search → open a server-loaded CDE detail', async ({ page }) => {
	await page.goto('/repositories/cadsr');
	await expect(page.getByRole('link', { name: '2001' })).toBeVisible();

	await page.getByRole('searchbox').fill('tumor');
	await page.getByRole('button', { name: 'Search' }).click();
	await expect(page).toHaveURL('/repositories/cadsr?q=tumor');
	await expect(page.getByText(/Results for .*tumor/)).toBeVisible();
	await expect(page.getByText('Tumor Stage Code')).toBeVisible();

	await page.getByRole('link', { name: 'Tumor Stage Code' }).click();
	await expect(page).toHaveURL(/\/repositories\/cadsr\/2001/);
	await expect(page.getByText('The stage of a tumor.')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Tumor Stage' })).toBeVisible();
});

test('ClinicalTrials: URL search → open a server-loaded trial', async ({ page }) => {
	await page.goto('/repositories/clinicaltrials');
	await page.getByRole('searchbox').fill('melanoma');
	await page.getByRole('button', { name: 'Search' }).click();
	await expect(page).toHaveURL('/repositories/clinicaltrials?q=melanoma');
	await expect(page.getByRole('link', { name: 'NCT01234567' })).toBeVisible();
	await page.reload();
	await expect(page.getByRole('searchbox')).toHaveValue('melanoma');

	await page.getByRole('link', { name: 'A Study of Widgetinib' }).click();
	await expect(page).toHaveURL(/\/repositories\/clinicaltrials\/NCT01234567/);
	await expect(page.getByText('A Phase 2 Study of Widgetinib in Melanoma')).toBeVisible();
	await expect(page.getByText('Adults with measurable disease')).toBeVisible();
});

test('PubMed: copied URL restores search → open a server-loaded article', async ({ page }) => {
	await page.goto('/repositories/pubmed');
	await page.getByRole('searchbox').fill('immunotherapy');
	await page.getByRole('button', { name: 'Search' }).click();
	await expect(page).toHaveURL('/repositories/pubmed?q=immunotherapy');
	await expect(page.getByRole('link', { name: 'SSR article for immunotherapy' })).toBeVisible();
	await page.reload();
	await expect(page.getByRole('searchbox')).toHaveValue('immunotherapy');

	await page.getByRole('link', { name: 'SSR article for immunotherapy' }).click();
	await expect(page).toHaveURL('/repositories/pubmed/12345678');
	await expect(page.getByText('SSR abstract from FastAPI.')).toBeVisible();
});

test('NCIt: server-loaded concept hydrates the browser-only graph explorer', async ({ page }) => {
	await page.goto('/repositories/ncit/C3262');
	await expect(page.getByText('SSR concept definition from FastAPI.')).toBeVisible();
	await expect(page.getByTitle('Layout preset')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Hide isolated' })).toBeVisible();
	await expect(page.getByTitle('Export as PNG')).toBeVisible();
	await expect(page.getByTitle('Toggle minimap')).toBeVisible();
	await expect(page.getByText('Network', { exact: true })).toBeVisible();
});
