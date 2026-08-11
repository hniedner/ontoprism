import { expect, test } from '@playwright/test';
import { writeFile } from 'node:fs/promises';

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

test('ForceAtlas layout stays below the main-thread Long Task threshold at representative sizes', async ({
	page
}, testInfo) => {
	type Timing = {
		code: string;
		handlerDurationMs: number;
		animationFrameDelayMs: number;
		longestTaskMs: number;
	};
	const timings: Timing[] = [];

	for (const code of ['CPERF186', 'CPERF400']) {
		await page.goto(`/repositories/ncit/${code}`);
		await expect(page.getByRole('button', { name: 'Re-layout' })).toBeVisible();
		const timing = await page.evaluate(async (currentCode) => {
			const longTasks: number[] = [];
			const observer = new PerformanceObserver((list) => {
				longTasks.push(...list.getEntries().map(({ duration }) => duration));
			});
			observer.observe({ type: 'longtask' });
			const button = document.querySelector<HTMLButtonElement>('button[aria-label="Re-layout"]');
			if (!button) throw new Error('Re-layout button is missing');
			const frameStart = performance.now();
			const nextFrame = new Promise<number>((resolve) => {
				requestAnimationFrame(() => resolve(performance.now() - frameStart));
			});
			const handlerStart = performance.now();
			button.click();
			const handlerDurationMs = performance.now() - handlerStart;
			const animationFrameDelayMs = await nextFrame;
			await new Promise((resolve) => setTimeout(resolve, 0));
			observer.disconnect();
			return {
				code: currentCode,
				handlerDurationMs,
				animationFrameDelayMs,
				longestTaskMs: Math.max(0, ...longTasks)
			};
		}, code);
		timings.push(timing);
	}

	const timingPath = testInfo.outputPath('layout-responsiveness.json');
	await writeFile(timingPath, `${JSON.stringify(timings, null, 2)}\n`, 'utf8');
	await testInfo.attach('layout-responsiveness.json', {
		path: timingPath,
		contentType: 'application/json'
	});
	for (const timing of timings) {
		expect(timing.handlerDurationMs, timing.code).toBeLessThan(50);
		expect(timing.longestTaskMs, timing.code).toBeLessThanOrEqual(50);
		expect(timing.animationFrameDelayMs, timing.code).toBeLessThan(100);
	}
});

test('active worker layouts are replaced and navigation kills the current owner', async ({ page }) => {
	const pageErrors: string[] = [];
	page.on('pageerror', (error) => pageErrors.push(error.message));

	await page.goto('/repositories/ncit/CPERF400');
	const canvas = page.locator('.graph-canvas');
	await expect(canvas).toHaveAttribute('aria-busy', 'true');
	const mostConnected = page.getByRole('heading', { name: 'Most connected' });
	const rankedNode = mostConnected.locator('xpath=following-sibling::ul[1]').getByRole('button').first();
	await rankedNode.click();
	const selectedCode = await page.locator('p.font-mono').textContent();
	expect(selectedCode).not.toBeNull();

	const layoutSelect = page.getByTitle('Layout preset');
	await layoutSelect.selectOption('noverlap');
	await expect(layoutSelect).toHaveValue('noverlap');
	await expect(canvas).toHaveAttribute('aria-busy', 'true');
	await layoutSelect.selectOption('forceatlas2');
	await expect(layoutSelect).toHaveValue('forceatlas2');
	await expect(canvas).toHaveAttribute('aria-busy', 'false', { timeout: 3_000 });
	await expect(page.locator('p.font-mono')).toHaveText(selectedCode!);
	await page.getByRole('button', { name: 'Re-layout' }).click();
	await expect(canvas).toHaveAttribute('aria-busy', 'true');

	await page.getByRole('link', { name: 'NCIt Browser' }).click();
	await expect(page).toHaveURL('/repositories/ncit');
	await page.getByRole('link', { name: 'SSR Neoplasm' }).click();
	await expect(page).toHaveURL('/repositories/ncit/C3262');
	const replacementCanvas = page.locator('.graph-canvas');
	await expect(replacementCanvas).toHaveAttribute('aria-busy', 'false');
	await page.waitForTimeout(1_600);
	await expect(page).toHaveURL('/repositories/ncit/C3262');
	expect(pageErrors).toEqual([]);
});
