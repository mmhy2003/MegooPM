"use client";

import { useCallback, useEffect, useState } from "react";
import { TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import {
  instanceSettings,
  type CustomPageSummary,
  type ErrorPageMode,
  type ErrorPageRead,
} from "@/lib/api";
import { describeError } from "@/components/settings/lib";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** Matches ERROR_COPY in the renderer; the card names what it is binding. */
const CODE_NAMES: Record<number, string> = {
  400: "Bad request",
  401: "Authentication required",
  403: "Access denied",
  404: "Not found",
  500: "Something went wrong",
  502: "Bad gateway",
  503: "Service unavailable",
  504: "Gateway timeout",
};

const MODE_LABELS: Record<ErrorPageMode, string> = {
  default: "MegooPM page",
  custom_page: "Custom page",
};

const MODES: ErrorPageMode[] = ["default", "custom_page"];

type Row = { code: number; mode: ErrorPageMode; custom_page_id: number | null };

function sameSet(a: Row[], b: Row[]): boolean {
  return (
    a.length === b.length &&
    a.every((row, i) => row.mode === b[i].mode && row.custom_page_id === b[i].custom_page_id)
  );
}

function toRows(list: ErrorPageRead[]): Row[] {
  return list.map((row) => ({
    code: row.code,
    mode: row.mode,
    custom_page_id: row.custom_page_id ?? null,
  }));
}

/**
 * What each common HTTP error is answered with, instance-wide.
 *
 * Saved as a whole set rather than row by row: the API replaces all eight, so
 * a per-row save would leave the operator guessing which took effect.
 */
export function ErrorPagesCard({ pages }: { pages: CustomPageSummary[] }) {
  const [stored, setStored] = useState<Row[] | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = toRows(await instanceSettings.listErrorPages());
      setStored(next);
      setRows(next);
      setError(null);
    } catch (err) {
      setError(describeError(err).message);
    }
  }, []);

  useEffect(() => {
    // Wrapped rather than called directly: the lint rule reads a bare call as
    // a synchronous setState, and the flag lets a late reply from an unmounted
    // card fall on the floor.
    let active = true;
    void (async () => {
      if (active) await load();
    })();
    return () => {
      active = false;
    };
  }, [load]);

  function patch(code: number, change: Partial<Row>) {
    setRows((current) => current.map((row) => (row.code === code ? { ...row, ...change } : row)));
  }

  async function save() {
    setSaving(true);
    try {
      const applied = toRows(
        await instanceSettings.updateErrorPages(
          rows.map((row) => ({
            code: row.code,
            mode: row.mode,
            // Never send a page the mode does not use: the API rejects it, and
            // the payload would describe two configurations at once.
            custom_page_id: row.mode === "custom_page" ? row.custom_page_id : null,
          })),
        ),
      );
      setStored(applied);
      setRows(applied);
      toast.success("Error pages saved");
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  const dirty = stored !== null && !sameSet(rows, stored);
  // A custom row with no page chosen is not yet a configuration. Saving it
  // would only earn a 422 naming the code.
  const incomplete = rows.some((row) => row.mode === "custom_page" && row.custom_page_id === null);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <TriangleAlert className="size-4" /> Error pages
        </CardTitle>
        <CardDescription>
          What a visitor sees when MegooPM itself answers with an error, on every domain this
          instance serves. Errors your own application returns are passed through untouched.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : stored === null ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-16">Code</TableHead>
                <TableHead>Meaning</TableHead>
                <TableHead className="w-44">Answer with</TableHead>
                <TableHead className="w-56">Page</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.code}>
                  <TableCell className="font-mono text-xs font-medium">{row.code}</TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {CODE_NAMES[row.code]}
                  </TableCell>
                  <TableCell>
                    <Select
                      value={row.mode}
                      onValueChange={(value) =>
                        patch(row.code, {
                          mode: value as ErrorPageMode,
                          custom_page_id: value === "custom_page" ? row.custom_page_id : null,
                        })
                      }
                      items={MODE_LABELS}
                    >
                      <SelectTrigger aria-label={`Answer for ${row.code}`} disabled={saving}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {MODES.map((mode) => (
                          <SelectItem key={mode} value={mode}>
                            {MODE_LABELS[mode]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>
                    {row.mode === "custom_page" ? (
                      <Select
                        value={row.custom_page_id === null ? "" : String(row.custom_page_id)}
                        onValueChange={(value) =>
                          patch(row.code, { custom_page_id: Number(value) })
                        }
                        items={Object.fromEntries(
                          pages.map((page) => [String(page.id), page.name]),
                        )}
                      >
                        <SelectTrigger
                          aria-label={`Page for ${row.code}`}
                          disabled={saving || pages.length === 0}
                        >
                          <SelectValue
                            placeholder={pages.length === 0 ? "No pages yet" : "Choose a page"}
                          />
                        </SelectTrigger>
                        <SelectContent>
                          {pages.map((page) => (
                            <SelectItem key={page.id} value={String(page.id)}>
                              {page.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
      <CardFooter className="justify-end">
        <Button onClick={() => void save()} disabled={!dirty || incomplete || saving}>
          {saving ? "Saving…" : "Save error pages"}
        </Button>
      </CardFooter>
    </Card>
  );
}
