const DEFAULT_TIMEOUT_MS = 5_000;

export function parseFastApiOrigin(configured: string | undefined): URL {
	if (!configured) throw new Error('ONTOPRISM_FASTAPI_ORIGIN is required');
	const origin = new URL(configured);
	if (
		!['http:', 'https:'].includes(origin.protocol) ||
		origin.username ||
		origin.password ||
		origin.pathname !== '/' ||
		origin.search ||
		origin.hash
	) {
		throw new Error('ONTOPRISM_FASTAPI_ORIGIN must be an HTTP(S) origin without credentials or a path');
	}
	return origin;
}

export function parseFastApiTimeout(configured: string | undefined): number {
	if (!configured) return DEFAULT_TIMEOUT_MS;
	const timeoutMs = Number(configured);
	if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60_000) {
		throw new Error('ONTOPRISM_FASTAPI_TIMEOUT_MS must be an integer from 1 through 60000');
	}
	return timeoutMs;
}
