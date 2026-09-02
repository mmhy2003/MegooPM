"use client";

import "flag-icons/css/flag-icons.min.css";
import { Users } from "lucide-react";

import type { VisitorSummary } from "@/lib/api";

/**
 * Recorded visitors and the countries they came from.
 *
 * Unlike the threat map, this is *all* traffic that reached a managed host —
 * not only what CrowdSec flagged.
 *
 * The card states its window explicitly: "9 visitors" means nothing without
 * knowing whether that is a day or a month, and the API clamps the window to
 * the retention limit, so it may not be the one that was asked for.
 */
/**
 * A country's flag, from the `flag-icons` CSS sprite set.
 *
 * Not a Unicode flag emoji: Chrome and Edge on Windows render those as the
 * two-letter code, so a large share of operators would see exactly what this
 * is meant to replace.
 *
 * The code is kept as the accessible name — a flag alone is unreadable to a
 * screen reader, and several flags are hard to tell apart at 16px.
 */
function Flag({ country }: { country: string }) {
  return (
    <span
      className={`fi fi-${country.toLowerCase()} shrink-0 rounded-[2px]`}
      style={{ width: "1.25rem", height: "0.9375rem" }}
      role="img"
      aria-label={country}
      title={country}
    />
  );
}

export function VisitorsCard({ visitors }: { visitors: VisitorSummary }) {
  const recorded = visitors.total_visitors > 0;
  const window =
    visitors.days === 1 ? "today" : `the last ${visitors.days} days`;

  return (
    <section className="space-y-3 rounded-xl border p-4">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <Users className="size-4 shrink-0" aria-hidden="true" />
          Visitors
        </h3>
        <p className="text-muted-foreground text-xs">
          Distinct addresses that reached a managed host over {window}.
        </p>
      </div>

      {!recorded ? (
        <p className="text-muted-foreground text-sm">
          No visitors recorded yet — counting starts after the next flush.
        </p>
      ) : (
        <div className="space-y-4">
          <div className="flex gap-6">
            <div className="space-y-0.5">
              <p className="text-2xl font-semibold tabular-nums">
                {visitors.total_visitors}
              </p>
              <p className="text-muted-foreground text-xs">distinct visitors</p>
            </div>
            <div className="space-y-0.5">
              <p className="text-2xl font-semibold tabular-nums">
                {visitors.total_requests}
              </p>
              <p className="text-muted-foreground text-xs">requests</p>
            </div>
          </div>

          {/* Side by side once there is room: stacked, the two short lists left
              most of a full-width card empty. */}
          <div className="grid gap-6 lg:grid-cols-2">
            {visitors.countries.length > 0 ? (
              <div className="space-y-1">
                <p className="text-muted-foreground text-xs">By country</p>
                <ol className="space-y-1">
                  {visitors.countries.slice(0, 8).map((row) => (
                    <li
                      key={row.country}
                      className="flex items-baseline gap-3 text-sm"
                    >
                      <Flag country={row.country} />
                      <span className="tabular-nums">{row.requests}</span>
                      <span className="text-muted-foreground text-xs">
                        {row.visitors} visitor{row.visitors === 1 ? "" : "s"}
                      </span>
                    </li>
                  ))}
                </ol>
              </div>
            ) : null}

            {visitors.top_ips.length > 0 ? (
              <div className="space-y-1">
                <p className="text-muted-foreground text-xs">
                  Busiest addresses
                </p>
                <ul className="space-y-1">
                  {visitors.top_ips.slice(0, 8).map((row) => (
                    <li
                      key={row.ip}
                      className="flex items-baseline gap-3 text-sm"
                    >
                      <span className="font-mono text-xs">{row.ip}</span>
                      <span className="text-muted-foreground text-xs">
                        {/* Never hidden: the gap between these and the country
                          list is real, unlocated traffic. */}
                        {row.country ? (
                          <Flag country={row.country} />
                        ) : (
                          "unknown"
                        )}
                      </span>
                      <span className="tabular-nums">{row.requests}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </section>
  );
}
