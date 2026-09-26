import { render, screen, fireEvent } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import ConstituentSource from './ConstituentSource.svelte';

it('shows a source gap without inventing a source link or policy decision', () => {
    render(ConstituentSource, { evidence: {
        run_id: 'run', concept_code: 'C1', axis: 'op:Laterality', filler_code: 'MINT-one', axis_source: 'nlp',
        support: 'not-source-backed', policy_choices: [], inferred_assertions: [], sources: []
    } });
    expect(screen.getByText('No exact linked stated filler source.')).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.queryByText(/Policy choices/)).not.toBeInTheDocument();
});

it('opens the exact stated genus locator and distinguishes policy choices', async () => {
    render(ConstituentSource, { evidence: {
        run_id: 'run', concept_code: 'C1', axis: 'op:Morphology', filler_code: 'C2', axis_source: 'parent',
        support: 'genus-backed', policy_choices: ['axis-assignment'], inferred_assertions: [],
        sources: [{ fact_id: 'fact', kind: 'genus', anchor_code: 'C1', group_id: 'group', depth: 0,
            role_code: null, filler_code: 'C2', occurrence_id: null, structural_path: [] }]
    } });
    await fireEvent.click(screen.getByText('genus-backed — source evidence'));
    expect(screen.getByText('Stated genus C2')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'NCIt definition C1' })).toHaveAttribute('href', '/repositories/ncit/C1');
    expect(screen.getByText('Policy choices needing corroboration: axis-assignment')).toBeInTheDocument();
});
