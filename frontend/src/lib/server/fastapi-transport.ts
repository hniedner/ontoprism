const MAX_RESPONSE_BYTES = 32 * 1024 * 1024;
const REQUEST_BODY_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const BODYLESS_STATUSES = new Set([101, 204, 205, 304]);
const HOP_BY_HOP_HEADERS = new Set([
	'connection',
	'keep-alive',
	'proxy-authenticate',
	'proxy-authorization',
	'proxy-connection',
	'te',
	'trailer',
	'transfer-encoding',
	'upgrade'
]);
const UNTRUSTED_FORWARDING_HEADERS = new Set([
	'forwarded',
	'via',
	'x-forwarded-for',
	'x-forwarded-host',
	'x-forwarded-port',
	'x-forwarded-proto',
	'x-real-ip'
]);

export interface FastApiTransport {
	readonly origin: URL;
	readonly timeoutMs: number;
	readonly icdoEntitlement?: string;
	readonly fetch: typeof fetch;
}

function connectionHeaders(source: Headers): Set<string> {
	return new Set(
		(source.get('connection') ?? '')
			.split(',')
			.map((name) => name.trim().toLowerCase())
			.filter(Boolean)
	);
}

function copyHeaders(source: Headers, omitted: ReadonlySet<string>): Headers {
	const nominated = connectionHeaders(source);
	const headers = new Headers();
	for (const [name, value] of source) {
		const lower = name.toLowerCase();
		if (!HOP_BY_HOP_HEADERS.has(lower) && !nominated.has(lower) && !omitted.has(lower)) {
			headers.append(name, value);
		}
	}
	return headers;
}

function requiresIcdoEntitlement(pathname: string): boolean {
	return (
		pathname.startsWith('/api/v1/icdo/') ||
		/^\/api\/v1\/ncit\/concepts\/[^/]+\/(mappings|decomposition)$/.test(pathname) ||
		pathname === '/api/v1/mappings/$translate' ||
		pathname === '/api/v1/refresh'
	);
}

function requestHeaders(
	source: Headers,
	clientAddress: string,
	pathname: string,
	icdoEntitlement: string | undefined
): Headers {
	const headers = copyHeaders(
		source,
		new Set([
			'content-length',
			'cookie',
			'host',
			'x-icdo-entitlement',
			...UNTRUSTED_FORWARDING_HEADERS
		])
	);
	headers.set('x-forwarded-for', clientAddress);
	if (icdoEntitlement && requiresIcdoEntitlement(pathname)) {
		headers.set('x-icdo-entitlement', icdoEntitlement);
	}
	return headers;
}

function responseHeaders(source: Headers, origin: URL): Headers {
	const headers = copyHeaders(source, new Set(['content-encoding', 'content-length', 'location']));
	const location = source.get('location');
	if (location) {
		const target = new URL(location, origin);
		if (target.origin === origin.origin && target.pathname.startsWith('/api/')) {
			headers.set('location', `${target.pathname}${target.search}${target.hash}`);
		}
	}
	return headers;
}

function gatewayFailure(status: 502 | 503 | 504, detail: string): Response {
	return Response.json({ detail }, { status });
}

export async function forwardFastApiWith(
	request: Request,
	apiPath: string,
	transport: FastApiTransport,
	clientAddress: string
): Promise<Response> {
	const upstream = new URL(apiPath, transport.origin);
	const method = request.method.toUpperCase();
	const init: RequestInit = {
		method,
		headers: requestHeaders(
			request.headers,
			clientAddress,
			upstream.pathname,
			transport.icdoEntitlement
		),
		redirect: 'manual',
		signal: AbortSignal.any([request.signal, AbortSignal.timeout(transport.timeoutMs)])
	};
	if (REQUEST_BODY_METHODS.has(method)) init.body = await request.arrayBuffer();

	try {
		const response = await transport.fetch(upstream, init);
		const declaredSize = Number(response.headers.get('content-length'));
		if (Number.isFinite(declaredSize) && declaredSize > MAX_RESPONSE_BYTES) {
			return gatewayFailure(502, 'FastAPI response is too large');
		}
		const body = new Uint8Array(await response.arrayBuffer());
		if (body.byteLength > MAX_RESPONSE_BYTES) {
			return gatewayFailure(502, 'FastAPI response is too large');
		}
		return new Response(method === 'HEAD' || BODYLESS_STATUSES.has(response.status) ? null : body, {
			status: response.status,
			statusText: response.statusText,
			headers: responseHeaders(response.headers, transport.origin)
		});
	} catch (reason) {
		if (reason instanceof DOMException && ['AbortError', 'TimeoutError'].includes(reason.name)) {
			return gatewayFailure(504, 'FastAPI request timed out');
		}
		return gatewayFailure(503, 'FastAPI is unreachable');
	}
}
