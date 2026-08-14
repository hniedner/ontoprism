import { getJson } from "$lib/api";
import { critical } from "$lib/server/critical-load";
import type { IcdoDetail } from "$lib/types";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ fetch, params }) => {
  const detail = await critical(
    getJson<IcdoDetail>(
      `/api/v1/icdo/${params.edition}/${params.axis}/concepts/${params.code}`,
      fetch,
    ),
  );
  return {
    ...detail,
    alignments: detail.ncit_alignments,
  };
};
