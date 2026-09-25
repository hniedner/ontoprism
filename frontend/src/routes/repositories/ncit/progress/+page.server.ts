import { ApiRequestError, getPublicationProgress } from '$lib/api';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
    try {
        return { progress: await getPublicationProgress(fetch) };
    } catch (error) {
        if (error instanceof ApiRequestError && error.status === 404) return { progress: null };
        throw error;
    }
};
