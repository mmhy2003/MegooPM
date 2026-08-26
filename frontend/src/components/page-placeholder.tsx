import type { LucideIcon } from "lucide-react";

/**
 * Foundation placeholder for a product area. Feature tickets replace the body
 * with the real table/detail views while keeping the header contract.
 */
export function PagePlaceholder({
  title,
  description,
  icon: Icon,
}: {
  title: string;
  description: string;
  icon: LucideIcon;
}) {
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="bg-muted text-muted-foreground flex size-10 items-center justify-center rounded-lg">
          <Icon className="size-5" />
        </div>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
          <p className="text-muted-foreground text-sm">{description}</p>
        </div>
      </div>

      <div className="border-border flex min-h-64 items-center justify-center rounded-xl border border-dashed p-10 text-center">
        <p className="text-muted-foreground text-sm">
          Nothing here yet — this view is wired into the shell and ready for its
          feature ticket.
        </p>
      </div>
    </div>
  );
}
