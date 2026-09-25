import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

describe('publication progress load', () => {
    it('returns a published run rather than hiding it as absent', async () => {
        const progress = { run_id: 'run-1', outcome_counts: { unknown: 2 } };
        const result = await load({ fetch: vi.fn().mockResolvedValue(
            new Response(JSON.stringify(progress), { status: 200 })
        ) } as unknown as Parameters<typeof load>[0]);
        expect(result).toEqual({ progress });
    });
    it('reports no publication without masking service failures', async () => {
        const invoke = (status: number) => load({
            fetch: vi.fn().mockResolvedValue(new Response('{}', { status }))
        } as unknown as Parameters<typeof load>[0]);
        await expect(invoke(404)).resolves.toEqual({ progress: null });
        await expect(invoke(503)).rejects.toMatchObject({ status: 503 });
    });
});
