import { env } from '$env/dynamic/private';
import { parseFastApiOrigin, parseFastApiTimeout } from './fastapi-config';
import { forwardFastApiWith, type FastApiTransport } from './fastapi-transport';

function configuredTransport(): FastApiTransport {
	return {
		origin: parseFastApiOrigin(env.ONTOPRISM_FASTAPI_ORIGIN),
		timeoutMs: parseFastApiTimeout(env.ONTOPRISM_FASTAPI_TIMEOUT_MS),
		fetch
	};
}

export function forwardFastApi(request: Request, apiPath: string): Promise<Response> {
	return forwardFastApiWith(request, apiPath, configuredTransport());
}
