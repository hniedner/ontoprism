import { expect, test } from '@playwright/test';

test('ICD-O text and behaviour filters combine before pagination', async ({ page }, testInfo) => {
	const errors: string[] = [];
	page.on('pageerror', (error) => errors.push(error.message));
	page.on('response', (response) => { if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`); });
	await page.goto('/repositories/icdo/3.2/morphology');
	const table = page.getByRole('region', { name: 'ICD-O repository results' });
	await expect(table).toHaveAttribute('aria-busy', 'false');
	const first = table.locator('tbody tr').first();
	const code = (await first.locator('td').nth(0).textContent())!.trim();
	const behaviour = (await first.locator('td').nth(3).textContent())!.trim();

	await table.getByRole('button', { name: 'Filter Code', exact: true }).click();
	const codeDialog = page.getByRole('dialog', { name: 'Code filter' });
	await codeDialog.getByRole('textbox', { name: 'Filter Code text' }).fill(code);
	await expect(page).toHaveURL((url) => url.searchParams.get('text_code') === code);
	await expect(table.locator('tbody tr')).toHaveCount(1);
	await expect(table.locator('tbody tr td').nth(0)).toHaveText(code);
	const textScreenshot = testInfo.outputPath('icdo-text.png');
	await page.screenshot({ path: textScreenshot });
	console.log(`ICD-O text screenshot: ${textScreenshot}`);

	await table.getByRole('button', { name: 'Filter Behaviour', exact: true }).click();
	const behaviourDialog = page.getByRole('dialog', { name: 'Behaviour filter' });
	await behaviourDialog.getByRole('textbox', { name: 'Filter Behaviour text' }).fill(behaviour);
	await behaviourDialog.getByRole('checkbox', { name: behaviour, exact: true }).check();
	await expect(page).toHaveURL((url) => url.searchParams.get('text_behaviour') === behaviour && url.searchParams.get('behaviour') === behaviour);
	await expect(table.locator('tbody tr')).toHaveCount(1);
	await expect(table.locator('tbody tr td').nth(3)).toHaveText(behaviour);
	const combinedScreenshot = testInfo.outputPath('icdo-combined.png');
	await page.screenshot({ path: combinedScreenshot });
	console.log(`ICD-O combined screenshot: ${combinedScreenshot}`);
	expect(errors).toEqual([]);
});
