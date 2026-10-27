import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";

interface ResourceState<T> {
  path: string | null;
  data: T | null;
  error: string;
  fetchedAt: number | null;
  refreshing: boolean;
}

/** Retain the last successful observation during polling; label failed refreshes. */
export function useJobResource<T>(path: string | null, intervalMs = 3000) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<ResourceState<T>>({
    path: null,
    data: null,
    error: "",
    fetchedAt: null,
    refreshing: false,
  });
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    if (!path) {
      setState({
        path,
        data: null,
        error: "",
        fetchedAt: null,
        refreshing: false,
      });
      return;
    }
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const controller = new AbortController();
    async function fetchResource() {
      setState((previous) => ({
        path,
        data: previous.path === path ? previous.data : null,
        error: previous.path === path ? previous.error : "",
        fetchedAt: previous.path === path ? previous.fetchedAt : null,
        refreshing: true,
      }));
      try {
        const result = await api<T>(path!, { signal: controller.signal });
        if (!active) return;
        failures = 0;
        setState({
          path,
          data: result,
          error: "",
          fetchedAt: Date.now(),
          refreshing: false,
        });
      } catch (reason) {
        if (!active || controller.signal.aborted) return;
        failures += 1;
        setState((previous) => ({
          ...previous,
          error:
            reason instanceof Error
              ? reason.message
              : "Could not refresh execution state.",
          refreshing: false,
        }));
      }
      if (active)
        timer = setTimeout(
          fetchResource,
          Math.min(15000, intervalMs * 2 ** failures),
        );
    }
    void fetchResource();
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [path, revision, intervalMs]);
  const current = state.path === path;
  return {
    data: current ? state.data : null,
    error: current ? state.error : "",
    loading: path !== null && (!current || (!state.data && state.refreshing)),
    refreshing: current && state.refreshing,
    fetchedAt: current ? state.fetchedAt : null,
    reload,
  };
}
