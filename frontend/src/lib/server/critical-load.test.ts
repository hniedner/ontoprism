import { describe, expect, it } from 'vitest';
import { ApiRequestError } from '$lib/api';
import { critical } from './critical-load';

describe('critical route loads', () => {
	it('returns successful domain data unchanged', async () => {
		await expect(critical(Promise.resolve({ code: 'C3262' }))).resolves.toEqual({ code: 'C3262' });
	});

	it('turns an API response failure into an expected SvelteKit HTTP error', async () => {
		await expect(critical(Promise.reject(new ApiRequestError(404, 'concept missing')))).rejects.toMatchObject(
			{
				status: 404,
				body: { message: 'concept missing' }
			}
		);
	});

	it('does not launder an unexpected implementation failure into an HTTP result', async () => {
		const defect = new Error('decoder defect');
		await expect(critical(Promise.reject(defect))).rejects.toBe(defect);
	});
});
