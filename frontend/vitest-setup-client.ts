// Client (jsdom) test setup: jest-dom matchers + DOM cleanup between tests.
import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/svelte';

afterEach(() => cleanup());
