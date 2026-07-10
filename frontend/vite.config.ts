import { defineConfig } from 'vitest/config';
import adapter from '@sveltejs/adapter-node';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) => filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			// adapter-auto only supports some environments, see https://svelte.dev/docs/kit/adapter-auto for a list.
			// If your environment is not supported, or you settled on a specific environment, switch out the adapter.
			// See https://svelte.dev/docs/kit/adapters for more information about adapters.
			adapter: adapter()
		})
	],
	server: {
		// Dev: proxy API calls to the FastAPI backend so the browser can use same-origin /api.
		proxy: {
			// ontoprism backend dev port (8001 is the sibling fairdata app).
			'/api': { target: 'http://localhost:8011', changeOrigin: true }
		}
	},
	test: {
		expect: { requireAssertions: true },
		coverage: {
			provider: 'v8',
			// Cover the shared library (components + logic). Routes are exercised by the
			// Playwright e2e flows, tracked separately; test files don't count.
			include: ['src/lib/**'],
			exclude: [
				'src/lib/**/*.{test,spec}.{js,ts}',
				'src/lib/vitest-examples/**',
				// Imperative WebGL (sigma) / 2d-canvas rendering shells: cannot mount in
				// jsdom (no WebGL/canvas). Their pure logic is extracted to and unit-tested
				// in src/lib/graph/graph-explorer.ts; the interactive behaviour is covered by
				// the Playwright e2e graph flows. See CLAUDE.local.md (documented exception).
				'src/lib/components/GraphExplorer.svelte',
				'src/lib/components/GraphMinimap.svelte'
			],
			thresholds: { lines: 90, functions: 90, branches: 90, statements: 90 },
			reporter: ['text', 'text-summary', 'json-summary']
		},
		projects: [
			{
				extends: './vite.config.ts',
				test: {
					name: 'server',
					environment: 'node',
					include: ['src/**/*.{test,spec}.{js,ts}'],
					exclude: ['src/**/*.svelte.{test,spec}.{js,ts}']
				}
			},
			{
				// Component tests (Svelte in jsdom); files named `*.svelte.test.ts`.
				extends: './vite.config.ts',
				// Use Svelte's browser build (not the SSR entry) so components mount.
				resolve: { conditions: ['browser'] },
				test: {
					name: 'client',
					environment: 'jsdom',
					include: ['src/**/*.svelte.{test,spec}.{js,ts}'],
					setupFiles: ['./vitest-setup-client.ts']
				}
			}
		]
	}
});
