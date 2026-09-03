"use client";

import { useEffect, useState } from "react";

/**
 * Return `value` only once it has held still for `delayMs`.
 *
 * For search boxes backed by a request: five characters typed quickly should
 * cost one round trip, not five. Client-side filters do not need this — they
 * are synchronous, and debouncing one only adds lag.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return settled;
}
