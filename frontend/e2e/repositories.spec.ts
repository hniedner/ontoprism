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

test('caDSR: concept graph precedes detail cards without loading eagerly', async ({ page }) => {
	const graphRequest = 'GET /api/v1/cadsr/cdes/6686721/neighborhood?depth=1';
	const countsBefore = (await (await page.request.get('/api/v1/__test__/counts')).json()) as Record<
		string,
		number
	>;

	await page.goto('/repositories/cadsr/6686721');
	const graphHeading = page.getByRole('heading', { name: 'Concept graph' });
	const cardsHeading = page.getByRole('heading', { name: /NCIt concepts/ });
	await expect(graphHeading).toBeVisible();
	await expect(cardsHeading).toBeVisible();
	expect(
		await graphHeading.evaluate(
			(graph, cards) =>
				Boolean(graph.compareDocumentPosition(cards as Node) & Node.DOCUMENT_POSITION_FOLLOWING),
			await cardsHeading.elementHandle()
		)
	).toBe(true);

	const countsAfter = (await (await page.request.get('/api/v1/__test__/counts')).json()) as Record<
		string,
		number
	>;
	expect(countsAfter[graphRequest] ?? 0).toBe(countsBefore[graphRequest] ?? 0);
	await expect(page.getByText('Mapped concept 7')).toHaveCount(0);
	await page.getByRole('button', { name: 'Show all 14 NCIt concepts' }).click();
	await expect(page.getByText('Mapped concept 7')).toBeVisible();
});

test('caDSR: delayed on-demand graph reserves its region while loading', async ({ page }) => {
	await page.goto('/repositories/cadsr/6686721');
	const graphSection = page.getByRole('heading', { name: 'Concept graph' }).locator('..').locator('..');
	await page.getByRole('button', { name: 'Explore in graph' }).click();

	const placeholder = graphSection.locator('[aria-busy="true"]');
	await expect(placeholder).toBeVisible();
	expect(await placeholder.evaluate((element) => getComputedStyle(element).minHeight)).toBe('512px');
	await expect(page.locator('.graph-canvas')).toBeVisible();
});

test('caDSR: one-column detail cards do not widen the viewport', async ({ page }) => {
	await page.setViewportSize({ width: 767, height: 900 });
	await page.goto('/repositories/cadsr/6686721');
	await expect(page.getByRole('heading', { name: 'Similar CDEs 10' })).toBeVisible();

	expect(
		await page.evaluate(() => ({
			documentWidth: document.documentElement.scrollWidth,
			viewportWidth: innerWidth
		}))
	).toEqual({ documentWidth: 767, viewportWidth: 767 });
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

test('ICD-O: entitled detail renders all publisher fields', async ({ page }) => {
	await page.goto('/repositories/icdo/3.2/morphology/ODUwMy8w');
	for (const value of [
		'Publisher note',
		'Code reference',
		'See also term',
		'See note',
		'Included term',
		'Excluded term',
		'Other publisher text'
	]) {
		await expect(page.getByRole('listitem').filter({ hasText: value })).toBeVisible();
	}
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

test('NCIt: route replacement owns graph state while an expansion is pending', async ({ page }) => {
	const intercepted = Promise.withResolvers<void>();
	const release = Promise.withResolvers<void>();
	await page.route('**/api/v1/ncit/concepts/C4005/neighborhood?*', async (route) => {
		intercepted.resolve();
		await release.promise;
		await route.fulfill({
			status: 503,
			contentType: 'application/json',
			body: JSON.stringify({ detail: 'stale expansion failure' })
		});
	});

	await page.goto('/repositories/ncit/C3262');
	const graphSearch = page.getByPlaceholder('Find node…');
	await graphSearch.fill('C4005');
	await graphSearch.press('Enter');
	await expect(page.getByRole('heading', { name: 'Unassessed neighbor', level: 4 })).toBeVisible();
	await page.getByRole('button', { name: 'Expand node' }).click();
	await intercepted.promise;
	await page.getByRole('button', { name: 'Open concept →' }).click();
	await expect(page).toHaveURL('/repositories/ncit/C4005');
	await expect(page.getByTitle('Layout preset')).toBeVisible();

	release.resolve();
	await expect(page.getByText('stale expansion failure')).toHaveCount(0);
	await expect(page.getByText('Network', { exact: true })).toBeVisible();
});

test('NCIt: published representation status survives browse, search, detail, and graph filters', async ({
	page
}) => {
	await page.goto('/repositories/ncit');
	await page.getByLabel('Representation status').selectOption('legacy-precoordinated');
	await expect(page).toHaveURL(
		'/repositories/ncit?representation_status=legacy-precoordinated'
	);
	await expect(page.getByText('Legacy pre-coordinated', { exact: true })).toBeVisible();

	await page.getByRole('searchbox', { name: 'Search NCIt' }).fill('neoplasm');
	await page.getByRole('button', { name: 'Search' }).click();
	await expect(page).toHaveURL(
		'/repositories/ncit?representation_status=legacy-precoordinated&q=neoplasm'
	);
	await page.getByRole('link', { name: 'SSR result for neoplasm' }).click();
	await expect(page.getByText('Legacy pre-coordinated', { exact: true }).first()).toBeVisible();

	const graphSearch = page.getByPlaceholder('Find node…');
	await graphSearch.fill('C3262');
	await graphSearch.press('Enter');
	await expect(page.getByRole('heading', { name: 'SSR Detail Concept', level: 4 })).toBeVisible();
	await expect(page.getByText('Legacy pre-coordinated', { exact: true })).toHaveCount(3);

	await page.getByRole('button', { name: 'Semantic type' }).click();
	await expect(page.locator('html')).toHaveClass(/dark/);
	await page.getByRole('button', { name: 'Toggle theme' }).click();
	await expect(page.locator('html')).not.toHaveClass(/dark/);
	await expect(page.getByText('Legacy pre-coordinated', { exact: true })).toHaveCount(3);
	await page.getByRole('button', { name: 'Communities' }).click();

	await page.getByRole('button', { name: 'Legacy pre-coordinated only' }).click();
	await expect(page.getByText('No graph nodes match the active filters.')).toHaveCount(0);
	await page.getByRole('button', { name: 'Disease', exact: true }).click();
	await expect(page.getByText('No graph nodes match the active filters.')).toHaveCount(0);

	await page.goto('/repositories/ncit/C4005');
	await page.getByRole('button', { name: 'Legacy pre-coordinated only' }).click();
	await expect(page.getByText('No graph nodes match the active filters.')).toBeVisible();
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
	// Camera updates change label-density settings while the layout worker mutates
	// node positions. This used to leave Sigma's program indices empty long enough
	// for a worker repaint to throw `node "…" can't be repaint`.
	for (let i = 0; i < 4; i += 1) await page.getByRole('button', { name: 'Zoom in' }).click();
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

test('graph label colors update in place and preserve selection across themes', async ({ page }) => {
	await page.addInitScript(() => {
		const fills: string[] = [];
		const originalFillText = CanvasRenderingContext2D.prototype.fillText;
		Object.defineProperty(window, '__sigmaLabelFills', { value: fills });
		CanvasRenderingContext2D.prototype.fillText = function (
			text: string,
			x: number,
			y: number,
			maxWidth?: number
		): void {
			fills.push(String(this.fillStyle));
			if (maxWidth === undefined) originalFillText.call(this, text, x, y);
			else originalFillText.call(this, text, x, y, maxWidth);
		};
	});

	await page.goto('/repositories/ncit/CPERF186');
	const canvas = page.locator('.graph-canvas');
	await expect(canvas).toHaveAttribute('aria-busy', 'false', { timeout: 3_000 });
	const mostConnected = page.getByRole('heading', { name: 'Most connected' });
	const rankedNode = mostConnected.locator('xpath=following-sibling::ul[1]').getByRole('button').first();
	await rankedNode.click();
	const selected = page.locator('h4').first();
	await expect(selected).toBeVisible();
	const selectedLabel = await selected.innerText();

	await expect
		.poll(() =>
			page.evaluate(() =>
				(
					window as typeof window & {
						__sigmaLabelFills: string[];
					}
				).__sigmaLabelFills.includes('#fafafa')
			)
		)
		.toBe(true);
	await page.evaluate(() => {
		const state = window as typeof window & {
			__sigmaCanvases?: HTMLCanvasElement[];
			__sigmaLabelFills: string[];
		};
		state.__sigmaCanvases = Array.from(document.querySelectorAll('.graph-canvas canvas'));
		state.__sigmaLabelFills.length = 0;
	});

	await page.getByRole('button', { name: 'Toggle theme' }).click();
	await expect(selected).toHaveText(selectedLabel);
	await expect
		.poll(() =>
			page.evaluate(() => {
				const state = window as typeof window & {
					__sigmaCanvases?: HTMLCanvasElement[];
					__sigmaLabelFills: string[];
				};
				const current = Array.from(document.querySelectorAll('.graph-canvas canvas'));
				return {
					lightLabelDrawn: state.__sigmaLabelFills.includes('#0d2140'),
					sameCanvases:
						state.__sigmaCanvases?.length === current.length &&
						state.__sigmaCanvases.every((item, index) => item === current[index])
				};
			})
		)
		.toEqual({ lightLabelDrawn: true, sameCanvases: true });
});
