import { formatCount, formatExact } from "@/lib/format";

/**
 * A count, shortened once it reaches a thousand.
 *
 * The exact figure is never lost: it is the hover title, and it is what a
 * screen reader announces — "1.3M" read aloud is "one point three M".
 */
export function Count({ value }: { value: number }) {
  const short = formatCount(value);
  if (Math.abs(value) < 1000) return <>{short}</>;

  const exact = formatExact(value);
  return (
    <span title={exact}>
      <span aria-hidden="true">{short}</span>
      <span className="sr-only">{exact}</span>
    </span>
  );
}
