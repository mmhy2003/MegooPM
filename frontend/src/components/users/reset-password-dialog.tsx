"use client";

import { useState } from "react";
import { toast } from "sonner";

import { users, type User } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { displayName, validateNewPassword } from "@/components/users/lib";
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

export function ResetPasswordDialog({
  open,
  onOpenChange,
  user,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user: User | null;
  onSaved: () => void;
}) {
  // The parent remounts this dialog with a `key` per target user, so the
  // fields start empty for each reset without an effect.
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit() {
    if (!user) return;
    const invalid = validateNewPassword(password, confirm);
    if (invalid) return setError(invalid);
    setError(null);
    setSaving(true);
    try {
      await users.resetPassword(user.id, { password });
      toast.success(`Password reset for ${displayName(user)}`);
      onOpenChange(false);
      onSaved();
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Reset password</DialogTitle>
          <DialogDescription>
            {user ? `Set a new password for ${displayName(user)}. Share it out of band.` : ""}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="reset-password">New password</Label>
            <Input
              id="reset-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={saving}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reset-confirm">Confirm password</Label>
            <Input
              id="reset-confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={saving}
            />
          </div>
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={saving}>
            {saving ? "Saving…" : "Set password"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
