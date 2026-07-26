import { expect, test } from '@playwright/test';

test('raw SPARQL navigation and route are unavailable', async ({ page }) => {
	const homeResponse = await page.goto('/');

	expect(homeResponse?.ok()).toBe(true);
	await expect(page.getByRole('link', { name: 'SPARQL', exact: true })).toHaveCount(0);

	const queryResponse = await page.goto('/query');

	expect(queryResponse?.status()).toBe(404);
});
