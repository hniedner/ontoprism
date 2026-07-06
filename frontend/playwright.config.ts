import { defineConfig, devices } from '@playwright/test';

// End-to-end tests run against the built SvelteKit app (vite preview). The backend is
// mocked via `page.route` in each spec, so no live API/data is required — the specs
// exercise the real rendered UI and client-side data flow.
const PORT = 4173;

export default defineConfig({
	testDir: 'e2e',
	fullyParallel: true,
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 1 : 0,
	reporter: process.env.CI ? 'line' : 'list',
	use: {
		baseURL: `http://localhost:${PORT}`,
		trace: 'on-first-retry'
	},
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
	webServer: {
		command: 'npm run build && npm run preview',
		port: PORT,
		reuseExistingServer: !process.env.CI,
		timeout: 120_000
	}
});
