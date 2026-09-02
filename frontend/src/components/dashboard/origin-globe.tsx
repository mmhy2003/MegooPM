"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { centroidFor } from "@/components/dashboard/country-centroids";
import { Button } from "@/components/ui/button";
import type { CountryCount, ThreatPoint } from "@/lib/api";

type Layer = "traffic" | "threats";

/** What the map draws, once a layer has been chosen. */
interface Origin {
  country: string;
  count: number;
}

/**
 * Where requests came from, as a globe with a ranked list beside it.
 *
 * Two layers, one at a time. They cannot be drawn together: both place a
 * country at the same centroid, so their markers would stack exactly and one
 * would silently hide the other, making the map under-report a whole layer.
 *
 * **The list is not decoration.** A WebGL canvas is invisible to assistive
 * technology, so the list is what makes this data available at all — and it is
 * what the tests assert on, which keeps them independent of the rendering
 * choice. If WebGL is unavailable the list stands alone.
 */
export function OriginGlobe({
  threats,
  traffic,
}: {
  threats: ThreatPoint[];
  traffic: CountryCount[];
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
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

  // Only countries the table knows can be drawn; the rest stay in the list.
  const plottable = useMemo(
    () =>
      active
        .map((row) => ({ ...row, position: centroidFor(row.country) }))
        .filter((row): row is Origin & { position: [number, number] } =>
          Boolean(row.position),
        ),
    [active],
  );
  const busiest = useMemo(
    () => Math.max(1, ...plottable.map((row) => row.count)),
    [plottable],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    let globe: { destroy: () => void; update: (s: { phi: number }) => void } | null =
      null;
    let frame = 0;
    let cancelled = false;

    void (async () => {
      try {
        const createGlobe = (await import("cobe")).default;
        if (cancelled) return;
        globe = createGlobe(canvas, {
          devicePixelRatio: 2,
          width: 400,
          height: 400,
          phi: 0,
          theta: 0.25,
          dark: 1,
          diffuse: 1.2,
          mapSamples: 16000,
          mapBrightness: 6,
          baseColor: [0.2, 0.2, 0.3],
          // Magenta for threats, cyan for traffic — the theme's two accents,
          // so the colour alone says which layer is showing.
          markerColor:
            layer === "threats" ? [1, 0.45, 0.8] : [0.35, 0.85, 0.95],
          glowColor: [0.3, 0.6, 0.7],
          markers: plottable.map((row) => ({
            location: row.position,
            // Scaled against the busiest origin so one country cannot swamp
            // the others into invisibility.
            size: 0.03 + (row.count / busiest) * 0.07,
          })),
        });

        // This cobe version has no onRender hook: rotation is driven by
        // calling update() from an animation frame.
        let phi = 0;
        const spin = () => {
          phi += 0.003;
          globe?.update({ phi });
          frame = requestAnimationFrame(spin);
        };
        frame = requestAnimationFrame(spin);
      } catch {
        // No WebGL, or the module failed to load. The list below still works.
        if (!cancelled) setCanDraw(false);
      }
    })();

    return () => {
      cancelled = true;
      if (frame) cancelAnimationFrame(frame);
      globe?.destroy();
    };
  }, [plottable, busiest, layer]);

  const empty = active.length === 0;

  return (
    <section className="space-y-3 rounded-xl border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">Request origins</h3>
          <p className="text-muted-foreground text-xs">
            {layer === "traffic"
              ? "Every request that reached a managed host."
              : "Where CrowdSec flagged requests came from — only what was flagged."}
          </p>
        </div>

        {/* Buttons rather than tabs: matched tab panels would duplicate the
            canvas element and rebuild the WebGL context on every switch. */}
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

      {empty ? (
        layer === "traffic" ? (
          <p className="text-muted-foreground text-sm">
            No visitors recorded yet — counting starts after the next flush.
          </p>
        ) : (
          <p className="text-muted-foreground text-sm">
            No attacks flagged. This means nothing was detected, not that nothing
            happened.
          </p>
        )
      ) : (
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
          {canDraw ? (
            <canvas
              ref={canvasRef}
              aria-hidden="true"
              className="size-[min(100%,20rem)] shrink-0"
              style={{ aspectRatio: "1" }}
            />
          ) : null}

          <ol className="min-w-0 flex-1 space-y-1">
            {active.map((row) => (
              <li key={row.country} className="flex items-baseline gap-3 text-sm">
                <span className="font-medium">{row.country}</span>
                <span className="tabular-nums">{row.count}</span>
                {centroidFor(row.country) === null ? (
                  <span className="text-muted-foreground text-xs">not located</span>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
