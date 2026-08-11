import { forwardFastApi } from '$lib/server/fastapi';
import type { RequestHandler } from './$types';

const proxy: RequestHandler = ({ params, request, url }) => {
	const path = `/api/${params.path ?? ''}${url.search}`;
	return forwardFastApi(request, path);
};

export const GET = proxy;
export const HEAD = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
