import "flag-icons/css/flag-icons.min.css";

/**
 * A country's flag, from the `flag-icons` CSS sprite set.
 *
 * Not a Unicode flag emoji: Chrome and Edge on Windows render those as the
 * two-letter code, so a large share of operators would see exactly what this
 * is meant to replace.
 *
 * The code is kept as the accessible name — a flag alone is unreadable to a
 * screen reader, and several flags are hard to tell apart at 16px. An
 * unknown country renders nothing at all, so a column of mixed rows does not
 * jitter around a placeholder.
 */
export function CountryFlag({
  country,
  className = "",
}: {
  country: string | null | undefined;
  className?: string;
}) {
  if (!country || country.length !== 2) return null;
  const code = country.toUpperCase();
  return (
    <span
      className={`fi fi-${code.toLowerCase()} inline-block shrink-0 rounded-[2px] align-[-2px] ${className}`}
      style={{ width: "1.25rem", height: "0.9375rem" }}
      role="img"
      aria-label={code}
      title={code}
    />
  );
}
