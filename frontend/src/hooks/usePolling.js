import { useCallback, useEffect, useRef, useState } from 'react';

// Runs `fn` immediately, then every `intervalMs` (skipped if falsy), storing
// the latest result/error. `refresh()` is exposed so a panel can force an
// immediate re-fetch right after a write it just performed (e.g. the audit
// log re-pulling itself after a manual adjustment).
export function usePolling(fn, intervalMs) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const refresh = useCallback(async () => {
    try {
      const result = await fnRef.current();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, []);

  useEffect(() => {
    refresh();
    if (!intervalMs) return undefined;
    const id = setInterval(refresh, intervalMs);
    return () => clearInterval(id);
  }, [refresh, intervalMs]);

  return { data, error, refresh };
}
