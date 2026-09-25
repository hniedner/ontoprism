import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

describe('publication progress load', () => {
    it('reports no publication without masking service failures', async () => {
        const invoke = (status: number) => load({
            fetch: vi.fn().mockResolvedValue(new Response('{}', { status }))
        } as unknown as Parameters<typeof load>[0]);
        await expect(invoke(404)).resolves.toEqual({ progress: null });
        await expect(invoke(503)).rejects.toMatchObject({ status: 503 });
    });
});
