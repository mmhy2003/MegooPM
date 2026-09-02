"use client";

import { useEffect, useMemo, useRef, useState } from "react";

// The library ships its own stylesheet; without it the map renders as
// unpositioned SVG.
import "jsvectormap/dist/jsvectormap.min.css";

import { Globe } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { CountryCount, ThreatPoint } from "@/lib/api";

type Layer = "traffic" | "threats";

/** What the map draws, once a layer has been chosen. */
interface Origin {
  country: string;
  count: number;
}

/** Bucket labels, lightest to strongest. Ordinal because jsvectormap's scale
 *  maps discrete labels to colours rather than interpolating a range. */
const BUCKETS = ["b1", "b2", "b3", "b4", "b5"] as const;

const TRAFFIC_SCALE: Record<string, string> = {
  b1: "#cfeff5",
  b2: "#8fdcea",
  b3: "#4fc3d9",
  b4: "#1f9fbd",
  b5: "#0b7391",
};

const THREAT_SCALE: Record<string, string> = {
  b1: "#ffd6ec",
  b2: "#ff9dd2",
  b3: "#f766b4",
  b4: "#d92f8f",
  b5: "#9c1263",
};

/**
 * Buckets each country into one of five bands, by share of the busiest.
 *
 * Linear on the maximum rather than on rank: with one dominant country the
 * rest would otherwise all land in the top band and the map would say
 * everywhere is equally busy.
 */
export function bucketFor(count: number, busiest: number): string {
  if (busiest <= 0) return BUCKETS[0];
  const share = count / busiest;
  const index = Math.min(
    BUCKETS.length - 1,
    Math.floor(share * BUCKETS.length),
  );
  return BUCKETS[Math.max(0, index)];
}

/**
 * Where requests came from, as a choropleth with a ranked list beside it.
 *
 * Two layers, one at a time, because both shade the same countries: drawn
 * together the second would simply overwrite the first.
 *
 * **The list is not decoration.** An SVG map built from region paths is not
 * meaningfully readable by assistive technology, so the list is what makes this
 * data available at all — and it is what the tests assert on, which keeps them
 * independent of the rendering library.
 */
export function OriginMap({
  threats,
  traffic,
}: {
  threats: ThreatPoint[];
  traffic: CountryCount[];
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [canDraw, setCanDraw] = useState(true);
  // Traffic first: it describes the whole site, where threats describe only
  // what CrowdSec flagged.
  const [layer, setLayer] = useState<Layer>("traffic");

  const active: Origin[] = useMemo(
    () =>
      layer === "traffic"
        ? traffic.map((row) => ({ country: row.country, count: row.requests }))
        : threats.map((row) => ({ country: row.country, count: row.count })),
    [layer, traffic, threats],
  );

  // Region codes, straight from the data. No centroid table: jsvectormap
  // shades the country itself, so nothing here needs to know where it is.
  const values = useMemo(() => {
    const busiest = Math.max(0, ...active.map((row) => row.count));
    return Object.fromEntries(
      active.map((row) => [
        row.country.toUpperCase(),
        bucketFor(row.count, busiest),
      ]),
    );
  }, [active]);

  useEffect(() => {
    const container = containerRef.current;
    // No early return on empty data: an unshaded world map says "nothing
    // from anywhere" far better than a sentence where a map should be, and
    // it does not look like a component that failed to load.
    if (!container) return;

    let map: { destroy: () => void } | null = null;
    let cancelled = false;

    void (async () => {
      try {
        const { default: JsVectorMap } = await import("jsvectormap");
        // Registers the "world" map on the constructor as a side effect.
        await import("jsvectormap/dist/maps/world");
        if (cancelled) return;

        container.innerHTML = "";
        map = new JsVectorMap({
          selector: container,
          map: "world",
          zoomButtons: false,
          regionStyle: {
            initial: { fill: "#2b3f49", stroke: "#1c2830", strokeWidth: 0.4 },
          },
          series: {
            regions: [
              {
                attribute: "fill",
                scale: layer === "traffic" ? TRAFFIC_SCALE : THREAT_SCALE,
                values,
              },
            ],
          },
        });
      } catch {
        // The library or the map data failed to load. The list below still
        // carries every number, so the panel degrades rather than blanking.
        if (!cancelled) setCanDraw(false);
      }
    })();

    return () => {
      cancelled = true;
      map?.destroy();
    };
  }, [values, layer]);

  const empty = active.length === 0;

  return (
    <section className="space-y-3 rounded-xl border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-medium">
            <Globe className="size-4 shrink-0" aria-hidden="true" />
            Request origins
          </h3>
          <p className="text-muted-foreground text-xs">
            {layer === "traffic"
              ? "Every request that reached a managed host."
              : "Where CrowdSec flagged requests came from — only what was flagged."}
          </p>
        </div>

        <div className="flex gap-1" role="group" aria-label="Map layer">
          {(["traffic", "threats"] as const).map((option) => (
            <Button
              key={option}
              size="sm"
              variant={layer === option ? "default" : "ghost"}
              aria-pressed={layer === option}
              onClick={() => setLayer(option)}
            >
              {option === "traffic" ? "Traffic" : "Threats"}
            </Button>
          ))}
        </div>
      </div>

      {/* Stacked, not side by side. Beside a full-width card the list was
          pushed to the far right and overflowed the card's edge, and the map
          was squeezed into a narrow column where it rendered tiny. Full width
          gives the map room; the list reads fine underneath. */}
      <div className="space-y-4">
        {canDraw ? (
          <div
            ref={containerRef}
            aria-hidden="true"
            className="h-[22rem] w-full sm:h-[30rem]"
          />
        ) : null}

        <div className="min-w-0">
          {empty ? (
            // Wording distinct from the Visitors card's: two panels repeating
            // one sentence reads as a bug rather than as two views of the same
            // absence.
            <p className="text-muted-foreground text-sm">
              {layer === "traffic"
                ? "Nothing shaded yet — no requests have been counted."
                : "Nothing shaded — CrowdSec has flagged no requests. That means nothing was detected, not that nothing happened."}
            </p>
          ) : (
            // Columns rather than one long list: below the map there is width
            // to spare, and a single column would push the card metres tall
            // once a few dozen countries appear.
            <ol className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3 lg:grid-cols-5">
              {active.map((row) => (
                <li
                  key={row.country}
                  className="flex items-baseline justify-between gap-3 text-sm"
                >
                  <span className="font-medium">{row.country}</span>
                  <span className="text-muted-foreground tabular-nums">
                    {row.count}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </section>
  );
}
