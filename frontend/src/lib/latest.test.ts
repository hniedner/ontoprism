import { describe, expect, it, vi } from 'vitest';
import { handleLatest } from './latest';

describe('handleLatest', () => {
	it('delivers the successful value and settlement while active', async () => {
		const handlers = { ready: vi.fn(), failed: vi.fn(), settled: vi.fn() };
		handleLatest(Promise.resolve('ready'), handlers);
		await vi.waitFor(() => expect(handlers.settled).toHaveBeenCalledOnce());
		expect(handlers.ready).toHaveBeenCalledWith('ready');
		expect(handlers.failed).not.toHaveBeenCalled();
	});

	it('delivers failure but suppresses every callback after cancellation', async () => {
		const failed = { ready: vi.fn(), failed: vi.fn(), settled: vi.fn() };
		handleLatest(Promise.reject(new Error('offline')), failed);
		await vi.waitFor(() => expect(failed.settled).toHaveBeenCalledOnce());
		expect(failed.failed).toHaveBeenCalledOnce();

		const pending = Promise.withResolvers<string>();
		const cancelled = { ready: vi.fn(), failed: vi.fn(), settled: vi.fn() };
		const cancel = handleLatest(pending.promise, cancelled);
		cancel();
		pending.resolve('late');
		await pending.promise;
		await Promise.resolve();
		expect(cancelled.ready).not.toHaveBeenCalled();
		expect(cancelled.failed).not.toHaveBeenCalled();
		expect(cancelled.settled).not.toHaveBeenCalled();
	});
});
