// Load the @testing-library/jest-dom matcher augmentations (toBeInTheDocument, …) so
// svelte-check/tsc sees them on vitest's `expect`, matching the runtime setup file.
import '@testing-library/jest-dom/vitest';
