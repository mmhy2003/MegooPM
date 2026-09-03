/**
 * The one substring matcher behind every list page's search box.
 *
 * Shared rather than hand-rolled per view because eleven separate `.filter()`
 * calls would drift in exactly the places that matter: whether case is folded,
 * whether the query is trimmed, and whether an array column like
 * `domain_names` is searched at all.
 *
 * Matching is a case-insensitive substring, not fuzzy: an operator typing
 * `api.example.com` wants that host, and six ranked near-misses are harder to
 * trust than a result that either contains the text or does not.
 *
 * `fields` returns strings, so a page with a numeric column — a stream's
 * incoming port — converts it at the call site. That keeps this function from
 * ever having to guess how to render a number, a date or an enum for matching.
 */
export function filterBySearch<T>(
  items: T[],
  query: string,
  fields: (item: T) => (string | null | undefined)[],
): T[] {
  const needle = query.trim().toLowerCase();
  // An empty or whitespace-only box is not a filter that matches nothing — it
  // is no filter at all.
  if (!needle) return items;
  return items.filter((item) =>
    fields(item).some(
      (field) => field != null && field.toLowerCase().includes(needle),
    ),
  );
}
