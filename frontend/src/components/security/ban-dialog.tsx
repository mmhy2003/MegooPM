"use client";

import { useState } from "react";
import { toast } from "sonner";

import {
  crowdsec,
  DECISION_DURATIONS,
  DECISION_SCOPE_LABELS,
  DECISION_SCOPES,
  DECISION_TYPE_LABELS,
  DECISION_TYPES,
  type DecisionScope,
  type DecisionType,
} from "@/lib/api";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const DEFAULT_DURATION = "4h";

export function BanDialog({
  open,
  onOpenChange,
  onSaved,
  initialValue = "",
  initialScope = "Ip",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
  /** Pre-fill the target (e.g. banning an offender straight from an alert). */
  initialValue?: string;
  initialScope?: DecisionScope;
}) {
  const [value, setValue] = useState(initialValue);
  const [scope, setScope] = useState<DecisionScope>(initialScope);
  const [type, setType] = useState<DecisionType>("ban");
  const [duration, setDuration] = useState(DEFAULT_DURATION);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSubmit() {
    setError(null);
    const target = value.trim();
    if (!target) {
      setError(scope === "Range" ? "Enter a CIDR range to ban." : "Enter an IP address to ban.");
      return;
    }
    if (/\s/.test(target)) {
      setError("The value must not contain spaces.");
      return;
    }
    if (scope === "Range" && !target.includes("/")) {
      setError("A range must be in CIDR notation, e.g. 10.0.0.0/24.");
      return;
    }

    setSaving(true);
    try {
      await crowdsec.addDecision({
        value: target,
        scope,
        type,
        duration,
        reason: reason.trim() || null,
      });
      toast.success(`${DECISION_TYPE_LABELS[type]} added for ${target}`);
      onOpenChange(false);
      onSaved();
    } catch (err) {
      const described = describeError(err);
      setError(described.message);
      toast.error(described.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Manual decision</DialogTitle>
          <DialogDescription>
            Push a decision to CrowdSec for an IP or range. This is recorded in the audit log
            and enforced by the bouncer until it expires or is lifted.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="ban-value">
              {scope === "Range" ? "CIDR range" : "IP address"}
            </Label>
            <Input
              id="ban-value"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={scope === "Range" ? "10.0.0.0/24" : "203.0.113.4"}
              disabled={saving}
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ban-scope">Scope</Label>
            <Select value={scope} onValueChange={(v) => setScope(v as DecisionScope)}>
              <SelectTrigger id="ban-scope" disabled={saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DECISION_SCOPES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {DECISION_SCOPE_LABELS[s]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ban-type">Remediation</Label>
            <Select value={type} onValueChange={(v) => setType(v as DecisionType)}>
              <SelectTrigger id="ban-type" disabled={saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DECISION_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {DECISION_TYPE_LABELS[t]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ban-duration">Duration</Label>
            <Select value={duration} onValueChange={(v) => setDuration(v ?? DEFAULT_DURATION)}>
              <SelectTrigger id="ban-duration" disabled={saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DECISION_DURATIONS.map((d) => (
                  <SelectItem key={d.value} value={d.value}>
                    {d.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ban-reason">Reason</Label>
            <Input
              id="ban-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Optional note"
              disabled={saving}
            />
          </div>
        </div>

        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={saving}>
            {saving ? "Adding…" : "Add decision"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
