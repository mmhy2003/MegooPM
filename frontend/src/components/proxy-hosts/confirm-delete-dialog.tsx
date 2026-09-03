"use client";

import { useState } from "react";
import { toast } from "sonner";

import { describeError } from "@/components/proxy-hosts/lib";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function ConfirmDeleteDialog({
  open,
  onOpenChange,
  title,
  description,
  onConfirm,
  onDeleted,
  confirmLabel = "Delete",
  successMessage = "Deleted",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  /** Performs the delete; may throw an `ApiError` to surface as a toast. */
  onConfirm: () => Promise<void>;
  onDeleted: () => void;
  /** The destructive button's label; the dialog is also used for other
      confirm-then-act flows (CrowdSec maintenance). */
  confirmLabel?: string;
  successMessage?: string;
}) {
  const [deleting, setDeleting] = useState(false);

  async function handleConfirm() {
    setDeleting(true);
    try {
      await onConfirm();
      toast.success(successMessage);
      onOpenChange(false);
      onDeleted();
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={deleting}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={handleConfirm} disabled={deleting}>
            {deleting ? "Working…" : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
