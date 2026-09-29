import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.SMOKE_FRONTEND_URL;
if (!baseURL?.startsWith('http://127.0.0.1:')) throw new Error('loopback frontend URL required');

export default defineConfig({
	testDir: 'e2e-real',
	fullyParallel: false,
	forbidOnly: true,
	retries: 0,
	reporter: 'list',
	use: { baseURL },
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }]
});
