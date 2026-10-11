import { defineEnvVars } from '@sveltejs/kit/env';

export const variables = defineEnvVars({
	// Read at runtime; the BFF validates the origin and timeout when handling a request.
	ONTOPRISM_FASTAPI_ORIGIN: { schema: (value) => value },
	ONTOPRISM_FASTAPI_TIMEOUT_MS: { schema: (value) => value },
	ICDO_ENTITLEMENT_KEY: { schema: (value) => value }
});
