"use client";

import { useState } from "react";
import { toast } from "sonner";

import { crowdsec, type Decision } from "@/lib/api";
import { describeError } from "@/components/security/lib";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function UnbanDialog({
  open,
  onOpenChange,
  decision,
  onLifted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The decision to lift; must carry an `id` (guaranteed by the caller). */
  decision: Decision;
  onLifted: () => void;
}) {
  const [lifting, setLifting] = useState(false);

  async function handleConfirm() {
    if (decision.id == null) {
      toast.error("This decision has no id and can't be lifted from here.");
      return;
    }
    setLifting(true);
    try {
      await crowdsec.deleteDecision(decision.id);
      toast.success(`Lifted ${decision.type} on ${decision.value}`);
      onOpenChange(false);
      onLifted();
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setLifting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Lift this decision?</DialogTitle>
          <DialogDescription>
            {decision.value} will no longer be{" "}
            {decision.type === "ban" ? "banned" : `subject to ${decision.type}`}. The removal is
            recorded in the audit log.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={lifting}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={handleConfirm} disabled={lifting}>
            {lifting ? "Lifting…" : "Lift decision"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
