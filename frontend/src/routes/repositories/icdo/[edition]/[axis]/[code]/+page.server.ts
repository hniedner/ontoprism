import { getJson } from "$lib/api";
import { critical } from "$lib/server/critical-load";
import { icdoFetch } from "$lib/server/icdo-fetch";
import type { IcdoDetail } from "$lib/types";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ cookies, fetch, params }) => {
  const detail = await critical(
    getJson<IcdoDetail>(
      `/api/v1/icdo/${params.edition}/${params.axis}/concepts/${params.code}`,
      icdoFetch(fetch, cookies),
    ),
  );
  return {
    ...detail,
    alignments: detail.ncit_alignments,
  };
};
