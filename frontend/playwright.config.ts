import { defineConfig, devices } from '@playwright/test';

// End-to-end tests run against the built adapter-node server. A real local FastAPI
// process supplies server-visible fixtures; browser interception alone cannot test SSR.
const PORT = 4173;
const REFUSED_PORT = 4174;
const FASTAPI_PORT = 18011;

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
	webServer: [
		{
			command: 'pdm run uvicorn test_support.remote_upstream_http:app --host 127.0.0.1 --port 18012',
			cwd: '..',
			url: 'http://127.0.0.1:18012/docs',
			reuseExistingServer: false,
			timeout: 120_000
		},
		{
			command: `env ENABLE_LICENSED_MAPPINGS=true pdm run uvicorn test_support.frontend_fastapi_double:app --host 127.0.0.1 --port ${FASTAPI_PORT}`,
			cwd: '..',
			url: `http://127.0.0.1:${FASTAPI_PORT}/health`,
			reuseExistingServer: false,
			timeout: 120_000
		},
		{
			command: `npm run build && env ONTOPRISM_FASTAPI_ORIGIN=http://127.0.0.1:${FASTAPI_PORT} ONTOPRISM_FASTAPI_TIMEOUT_MS=500 ICDO_ENTITLEMENT_KEY=licensed HOST=127.0.0.1 PORT=${PORT} ORIGIN=http://127.0.0.1:${PORT} node build`,
			port: PORT,
			reuseExistingServer: !process.env.CI,
			timeout: 120_000
		},
		{
			command: `env ONTOPRISM_FASTAPI_ORIGIN=http://127.0.0.1:${FASTAPI_PORT} ONTOPRISM_FASTAPI_TIMEOUT_MS=500 HOST=127.0.0.1 PORT=${REFUSED_PORT} ORIGIN=http://127.0.0.1:${REFUSED_PORT} node build`,
			port: REFUSED_PORT,
			reuseExistingServer: false,
			timeout: 120_000
		}
	]
});
