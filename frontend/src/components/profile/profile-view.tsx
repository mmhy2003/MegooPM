"use client";

import { useState } from "react";
import { CircleUser, LogOut } from "lucide-react";
import { toast } from "sonner";

import { users } from "@/lib/api";
import { useAuth } from "@/lib/auth/context";
import { describeError } from "@/components/proxy-hosts/lib";
import { validateNewPassword } from "@/components/users/lib";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function ProfileView() {
  const { user, refreshUser, logout } = useAuth();

  // --- name. `AuthGuard` only renders the shell once the session user is
  // known, so initialising from `user` here is safe; after a save we call
  // `refreshUser()` so the topbar avatar reflects the new name.
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [profileError, setProfileError] = useState<string | null>(null);
  const [savingProfile, setSavingProfile] = useState(false);

  async function saveProfile() {
    setProfileError(null);
    setSavingProfile(true);
    try {
      await users.updateMe({ full_name: fullName.trim() });
      await refreshUser();
      toast.success("Profile saved");
    } catch (err) {
      setProfileError(describeError(err).message);
    } finally {
      setSavingProfile(false);
    }
  }

  // --- password
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [savingPassword, setSavingPassword] = useState(false);

  async function changePassword() {
    const invalid = validateNewPassword(next, confirm);
    if (invalid) return setPasswordError(invalid);
    setPasswordError(null);
    setSavingPassword(true);
    try {
      await users.changeMyPassword({ new_password: next });
      setNext("");
      setConfirm("");
      toast.success("Password changed");
    } catch (err) {
      setPasswordError(describeError(err).message);
    } finally {
      setSavingPassword(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <CircleUser className="size-5" />
        </div>
        <div className="flex-1">
          <h2 className="text-xl font-semibold tracking-tight">Profile</h2>
          <p className="text-sm text-muted-foreground">Your name and sign-in password.</p>
        </div>
        <Button variant="outline" size="sm" onClick={logout}>
          <LogOut /> Sign out
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Name</CardTitle>
          <CardDescription>How your name appears in the app and in the audit log.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="account-email">Email</Label>
            <Input id="account-email" value={user?.email ?? ""} readOnly disabled />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="account-name">Full name</Label>
            <Input
              id="account-name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              disabled={savingProfile}
            />
          </div>
          {profileError ? (
            <p role="alert" className="text-sm text-destructive">
              {profileError}
            </p>
          ) : null}
        </CardContent>
        <CardFooter className="justify-end">
          <Button onClick={saveProfile} disabled={savingProfile}>
            {savingProfile ? "Saving…" : "Save name"}
          </Button>
        </CardFooter>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Password</CardTitle>
          <CardDescription>Choose a new password (at least 8 characters).</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="account-new">New password</Label>
            <Input
              id="account-new"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              disabled={savingPassword}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="account-confirm">Confirm new password</Label>
            <Input
              id="account-confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={savingPassword}
            />
          </div>
          {passwordError ? (
            <p role="alert" className="text-sm text-destructive">
              {passwordError}
            </p>
          ) : null}
        </CardContent>
        <CardFooter className="justify-end">
          <Button onClick={changePassword} disabled={savingPassword}>
            {savingPassword ? "Saving…" : "Change password"}
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
