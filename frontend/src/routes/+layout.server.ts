import { readIcdoAccess } from '#lib/server/icdo-access.ts';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ fetch }) => ({
	icdoAccess: await readIcdoAccess(fetch)
});
