import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";

export type ApiData<T> =
  | { status: "loading"; data: undefined; error: undefined }
  | { status: "success"; data: T; error: undefined }
  | { status: "error"; data: undefined; error: ApiError };

function toApiError(err: unknown): ApiError {
  return err instanceof ApiError
    ? err
    : new ApiError(0, "unexpected", "Something went wrong while loading this page.");
}

/** Loads data from the API whenever `deps` change; `reload` re-runs the request. */
export function useApiData<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): ApiData<T> & { reload: () => void } {
  const [state, setState] = useState<ApiData<T>>({ status: "loading", data: undefined, error: undefined });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading", data: undefined, error: undefined });
    load(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ status: "success", data, error: undefined });
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setState({ status: "error", data: undefined, error: toApiError(err) });
      },
    );
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...state, reload };
}
