import { getPublicationProgress } from '$lib/api';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => ({
	progress: await getPublicationProgress(fetch)
});
