"use client";

import { useState } from "react";
import { toast } from "sonner";

import { accessLists, type AccessList } from "@/lib/api";
import { describeError, satisfyDescription } from "@/components/access-lists/lib";
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
import { Switch } from "@/components/ui/switch";

/**
 * Create a new access list (name + gate settings only). Users and IP rules are
 * added afterwards in the editor — the parent opens it on the returned list.
 */
export function AccessListDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (list: AccessList) => void;
}) {
  const [name, setName] = useState("");
  const [satisfyAny, setSatisfyAny] = useState(false);
  const [passAuth, setPassAuth] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSubmit() {
    setError(null);
    if (!name.trim()) {
      setError("Enter a name for the access list.");
      return;
    }
    setSaving(true);
    try {
      const created = await accessLists.create({
        name: name.trim(),
        satisfy_any: satisfyAny,
        pass_auth: passAuth,
      });
      toast.success("Access list created");
      onOpenChange(false);
      onCreated(created);
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
          <DialogTitle>New access list</DialogTitle>
          <DialogDescription>
            An authorization gate you can attach to one or more proxy hosts. Add
            basic-auth users and IP rules after creating it.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="al-name">Name</Label>
            <Input
              id="al-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Internal only"
              disabled={saving}
              autoFocus
            />
          </div>

          <label className="flex items-start gap-2">
            <Switch
              checked={satisfyAny}
              onCheckedChange={setSatisfyAny}
              disabled={saving}
            />
            <span className="space-y-0.5">
              <span className="block text-sm font-medium leading-none">
                Satisfy Any
              </span>
              <span className="block text-xs text-muted-foreground">
                {satisfyDescription(satisfyAny)}
              </span>
            </span>
          </label>

          <label className="flex items-start gap-2">
            <Switch
              checked={passAuth}
              onCheckedChange={setPassAuth}
              disabled={saving}
            />
            <span className="space-y-0.5">
              <span className="block text-sm font-medium leading-none">
                Pass Auth
              </span>
              <span className="block text-xs text-muted-foreground">
                Forward the Authorization header to the upstream instead of
                stripping it.
              </span>
            </span>
          </label>
        </div>

        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={saving}>
            {saving ? "Creating…" : "Create list"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
