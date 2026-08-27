"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { USER_ROLES, USER_ROLE_LABELS, users, type User, type UserRole } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { isSelf, MIN_PASSWORD_LENGTH } from "@/components/users/lib";
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
import { Switch } from "@/components/ui/switch";

export function UserDialog({
  open,
  onOpenChange,
  user,
  currentUser,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode; otherwise the user being edited. */
  user: User | null;
  /** The signed-in user; your own role/active controls are disabled. */
  currentUser: User | null;
  onSaved: () => void;
}) {
  const isEdit = user !== null;
  const editingSelf = user !== null && isSelf(user, currentUser);

  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState<UserRole>("member");
  const [isActive, setIsActive] = useState(true);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Reset the form whenever the dialog (re)opens for a different user.
  useEffect(() => {
    if (!open) return;
    setEmail(user?.email ?? "");
    setFullName(user?.full_name ?? "");
    setRole(user?.role ?? "member");
    setIsActive(user?.is_active ?? true);
    setPassword("");
    setError(null);
  }, [open, user]);

  async function submit() {
    setError(null);
    if (!isEdit) {
      if (!email.trim()) return setError("Enter an email address.");
      if (password.length < MIN_PASSWORD_LENGTH) {
        return setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      }
    }

    setSaving(true);
    try {
      if (isEdit) {
        await users.update(user.id, {
          full_name: fullName.trim(),
          ...(editingSelf ? {} : { role, is_active: isActive }),
        });
        toast.success("User updated");
      } else {
        await users.create({
          email: email.trim(),
          password,
          full_name: fullName.trim(),
          role,
          is_active: isActive,
        });
        toast.success("User created");
      }
      onOpenChange(false);
      onSaved();
    } catch (err) {
      const described = describeError(err);
      setError(described.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit user" : "New user"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Change the display name, role, or whether the account can sign in."
              : "Create an account. Share the password with the person out of band."}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="user-email">Email</Label>
            <Input
              id="user-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="person@example.com"
              disabled={saving || isEdit}
              readOnly={isEdit}
            />
            {isEdit ? (
              <p className="text-xs text-muted-foreground">
                Email is the account identity and cannot be changed.
              </p>
            ) : null}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="user-name">Full name</Label>
            <Input
              id="user-name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Optional"
              disabled={saving}
            />
          </div>
          {!isEdit ? (
            <div className="space-y-1.5">
              <Label htmlFor="user-password">Password</Label>
              <Input
                id="user-password"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={saving}
              />
            </div>
          ) : null}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="user-role">Role</Label>
              <Select value={role} onValueChange={(v) => setRole(v as UserRole)}>
                <SelectTrigger id="user-role" disabled={saving || editingSelf}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {USER_ROLES.map((r) => (
                    <SelectItem key={r} value={r}>
                      {USER_ROLE_LABELS[r]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <label className="flex items-center gap-2 self-end pb-2">
              <Switch
                checked={isActive}
                onCheckedChange={setIsActive}
                disabled={saving || editingSelf}
              />
              <span className="text-sm font-medium">Active</span>
            </label>
          </div>
          {editingSelf ? (
            <p className="text-xs text-muted-foreground">
              You cannot change your own role or deactivate your own account.
            </p>
          ) : null}

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
            {saving ? "Saving…" : isEdit ? "Save changes" : "Create user"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
