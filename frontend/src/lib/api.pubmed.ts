// PubMed API client. Built on the shared request helpers from the core api module.

import { apiUrl, getJson, postJsonBody } from './api';
import type { PubMedArticleDetail, PubMedSearchResult, RelatedArticlesResult } from './types';

/** Search PubMed and return resolved article summaries. */
export function searchPubmed(
	query: string,
	retmax = 20,
	fetchImpl?: typeof fetch
): Promise<PubMedSearchResult> {
	return postJsonBody<PubMedSearchResult>(
		apiUrl('/api/v1/pubmed/search'),
		{ query, retmax },
		fetchImpl
	);
}

/** Fetch one article's full detail by PMID. */
export function getArticle(pmid: string, fetchImpl?: typeof fetch): Promise<PubMedArticleDetail> {
	return getJson<PubMedArticleDetail>(apiUrl(`/api/v1/pubmed/${encodeURIComponent(pmid)}`), fetchImpl);
}

/** Fetch related-article PMIDs (similar / cited_by / references) for an article. */
export function getRelatedArticles(
	pmid: string,
	linkType: 'similar' | 'cited_by' | 'references' = 'similar',
	fetchImpl?: typeof fetch
): Promise<RelatedArticlesResult> {
	return getJson<RelatedArticlesResult>(
		apiUrl(`/api/v1/pubmed/${encodeURIComponent(pmid)}/related`, { link_type: linkType }),
		fetchImpl
	);
}
