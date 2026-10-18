"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchMe, UnauthenticatedError } from "@/lib/api/account";
import { queryKeys } from "@/lib/api/query-keys";

/** True once `GET /me` has answered 401 (no session). Shares the shell's `me` cache entry, so it
 * costs no extra request on a signed-in page; a locked panel uses it to pick sign-in over upgrade. */
export function useIsAnonymous(): boolean {
  const query = useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => fetchMe(signal),
    retry: (failureCount, error) =>
      error instanceof UnauthenticatedError ? false : failureCount < 1,
  });
  return query.error instanceof UnauthenticatedError;
}
