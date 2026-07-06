import { expect, test, type Page } from '@playwright/test';

// A minimal fake backend: intercept the API calls each flow makes and return canned
// payloads shaped like the real read models, so the specs exercise the rendered UI
// end-to-end without a live backend or seeded data.

async function mockCadsr(page: Page): Promise<void> {
	await page.route('**/api/v1/cadsr/list**', (route) =>
		route.fulfill({
			json: {
				query: '',
				total: 1,
				limit: 25,
				offset: 0,
				hits: [
					{
						public_id: '2001',
						version: '1.0',
						short_name: 'TUMOR_STAGE',
						long_name: 'Tumor Stage Code',
						context: 'NCIP',
						datatype: 'CHARACTER'
					}
				]
			}
		})
	);
	await page.route('**/api/v1/cadsr/search**', (route) =>
		route.fulfill({
			json: {
				query: 'tumor',
				total: 1,
				limit: 25,
				offset: 0,
				hits: [
					{
						public_id: '2001',
						version: '1.0',
						short_name: 'TUMOR_STAGE',
						long_name: 'Tumor Stage Code',
						context: 'NCIP',
						datatype: 'CHARACTER'
					}
				]
			}
		})
	);
	await page.route('**/api/v1/cadsr/cdes/2001/similar**', (route) =>
		route.fulfill({ json: [] })
	);
	await page.route('**/api/v1/cadsr/cdes/2001', (route) =>
		route.fulfill({
			json: {
				public_id: '2001',
				version: '1.0',
				short_name: 'TUMOR_STAGE',
				long_name: 'Tumor Stage Code',
				context: 'NCIP',
				datatype: 'CHARACTER',
				definition: 'The stage of a tumor.',
				workflow_status: 'RELEASED',
				registration_status: 'Standard',
				value_domain_type: 'Enumerated',
				permissible_values: [{ value: 'I', meaning: 'Stage I', meaning_code: 'C1' }],
				concepts: [
					{
						concept_code: 'C48885',
						concept_name: 'Tumor Stage',
						concept_type: 'objectClass',
						is_primary: true
					}
				]
			}
		})
	);
}

test('caDSR: browse → search → open a CDE detail', async ({ page }) => {
	await mockCadsr(page);
	await page.goto('/repositories/cadsr');

	// Browse mode loads on mount.
	await expect(page.getByRole('link', { name: '2001' })).toBeVisible();

	// Search narrows to the same CDE — assert the results card flips to search mode
	// (only set after a real search request), not just that the row is present.
	await page.getByRole('searchbox').fill('tumor');
	await page.getByRole('button', { name: 'Search' }).click();
	await expect(page.getByText(/Results for .*tumor/)).toBeVisible();
	await expect(page.getByText('Tumor Stage Code')).toBeVisible();

	// Open the detail page.
	await page.getByRole('link', { name: 'Tumor Stage Code' }).click();
	await expect(page).toHaveURL(/\/repositories\/cadsr\/2001/);
	await expect(page.getByText('The stage of a tumor.')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Tumor Stage' })).toBeVisible(); // NCIt concept
});

test('ClinicalTrials: search by condition → open a trial', async ({ page }) => {
	await page.route('**/api/v1/clinicaltrials/search**', (route) =>
		route.fulfill({
			json: {
				condition: 'melanoma',
				intervention: null,
				term: null,
				total: 1,
				studies: [
					{
						nct_id: 'NCT01234567',
						title: 'A Study of Widgetinib',
						status: 'RECRUITING',
						phase: 'PHASE2',
						conditions: ['Melanoma'],
						interventions: ['Widgetinib'],
						start_date: '2024-01-01',
						enrollment: 100,
						relevance_score: 1.0
					}
				]
			}
		})
	);
	await page.route('**/api/v1/clinicaltrials/NCT01234567', (route) =>
		route.fulfill({
			json: {
				nct_id: 'NCT01234567',
				title: 'A Study of Widgetinib',
				official_title: 'A Phase 2 Study of Widgetinib in Melanoma',
				status: 'RECRUITING',
				phase: 'PHASE2',
				study_type: 'INTERVENTIONAL',
				primary_purpose: 'TREATMENT',
				conditions: ['Melanoma'],
				interventions: [{ type: 'DRUG', name: 'Widgetinib', description: null }],
				primary_outcomes: [],
				secondary_outcomes: [],
				eligibility_criteria: 'Adults with measurable disease',
				enrollment: 100,
				start_date: '2024-01-01',
				sponsors: [{ name: 'Acme Oncology', role: 'lead' }],
				locations: [],
				references: [],
				url: 'https://clinicaltrials.gov/study/NCT01234567'
			}
		})
	);

	await page.goto('/repositories/clinicaltrials');
	await page.getByRole('searchbox').fill('melanoma');
	await page.getByRole('button', { name: 'Search' }).click();
	await expect(page.getByRole('link', { name: 'NCT01234567' })).toBeVisible();

	await page.getByRole('link', { name: 'A Study of Widgetinib' }).click();
	await expect(page).toHaveURL(/\/repositories\/clinicaltrials\/NCT01234567/);
	await expect(page.getByText('A Phase 2 Study of Widgetinib in Melanoma')).toBeVisible();
	await expect(page.getByText('Adults with measurable disease')).toBeVisible();
});

test('NCIt: concept page mounts the graph explorer with its controls', async ({ page }) => {
	await page.route('**/api/v1/ncit/concepts/C3262/neighborhood**', (route) =>
		route.fulfill({
			json: {
				center: 'C3262',
				nodes: [
					{ code: 'C3262', label: 'Neoplasm', semantic_type: 'Neoplastic Process' },
					{ code: 'C12922', label: 'Neoplastic Cell', semantic_type: null }
				],
				edges: [
					{
						source: 'C3262',
						target: 'C12922',
						relation: 'R105',
						relation_label: 'Disease_Has_Abnormal_Cell',
						kind: 'role'
					}
				],
				truncated: false
			}
		})
	);
	await page.route('**/api/v1/ncit/concepts/C3262/similar**', (route) => route.fulfill({ json: [] }));
	await page.route('**/api/v1/ncit/concepts/C3262', (route) =>
		route.fulfill({
			json: {
				code: 'C3262',
				label: 'Neoplasm',
				preferred_name: 'Neoplasm',
				definition: 'A tissue growth.',
				semantic_types: ['Neoplastic Process'],
				synonyms: ['Neoplasia'],
				parents: [],
				children: [],
				roles: [],
				associations: [],
				incoming_roles: []
			}
		})
	);

	await page.goto('/repositories/ncit/C3262');
	// Toolbar + panel render regardless of the WebGL canvas: assert the new controls.
	await expect(page.getByTitle('Layout preset')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Hide isolated' })).toBeVisible();
	await expect(page.getByTitle('Export as PNG')).toBeVisible();
	await expect(page.getByTitle('Toggle minimap')).toBeVisible();
	await expect(page.getByText('Network', { exact: true })).toBeVisible();
});
