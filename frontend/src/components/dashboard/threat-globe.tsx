"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { ThreatPoint } from "@/lib/api";

/**
 * Attack origins, drawn on a globe with a ranked list beside it.
 *
 * **The list is not decoration.** A WebGL canvas is invisible to assistive
 * technology, so the list is what makes this data available at all — and it is
 * what the tests assert on, which keeps them independent of the rendering
 * choice. If WebGL is unavailable the list stands alone rather than leaving a
 * blank box.
 *
 * `cobe` was chosen by measurement: 32 KB on disk against the several hundred
 * that a three.js globe adds to a page whose entire purpose is five numbers.
 */
export function ThreatGlobe({ points }: { points: ThreatPoint[] }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [canDraw, setCanDraw] = useState(true);

  // Only points that were actually geolocated can be drawn; the rest are still
  // counted in the list.
  //
  // Memoised because the effect below depends on it: a fresh array every render
  // would tear the globe down and rebuild it on each poll, which both flickers
  // and leaks WebGL contexts.
  const plottable = useMemo(
    () => points.filter((p) => p.lat !== null && p.lng !== null),
    [points],
  );
  const busiest = useMemo(
    () => Math.max(1, ...plottable.map((p) => p.count)),
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
          markerColor: [1, 0.45, 0.8],
          glowColor: [0.3, 0.6, 0.7],
          markers: plottable.map((p) => ({
            location: [p.lat as number, p.lng as number] as [number, number],
            // Scaled against the busiest origin so one country cannot swamp the
            // others into invisibility.
            size: 0.03 + (p.count / busiest) * 0.07,
          })),
        });

        // This cobe version has no onRender hook: rotation is driven by calling
        // update() from an animation frame.
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
  }, [plottable, busiest]);

  return (
    <section className="space-y-3 rounded-xl border p-4">
      <div>
        <h3 className="text-sm font-medium">Attack origins</h3>
        <p className="text-muted-foreground text-xs">
          Where CrowdSec flagged requests came from. Not all traffic — only what was
          flagged.
        </p>
      </div>

      {points.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No attacks flagged. This means nothing was detected, not that nothing
          happened.
        </p>
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
            {points.map((point) => (
              <li key={point.country} className="flex items-baseline gap-3 text-sm">
                <span className="font-medium">{point.country}</span>
                <span className="tabular-nums">{point.count}</span>
                {point.lat === null ? (
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
