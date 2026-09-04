"use client";

import { useCallback, useEffect, useState } from "react";
import {
  KeyRound,
  MailPlus,
  Pencil,
  Plus,
  Send,
  ShieldOff,
  Trash2,
  Users as UsersIcon,
} from "lucide-react";
import { toast } from "sonner";

import { USER_ROLE_LABELS, users, type User } from "@/lib/api";
import { fetchCapabilities } from "@/lib/auth/api";
import { useAuth } from "@/lib/auth/context";
import { describeError } from "@/components/proxy-hosts/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import { displayName, isSelf } from "@/components/users/lib";
import { ResetPasswordDialog } from "@/components/users/reset-password-dialog";
import { InviteDialog } from "@/components/users/invite-dialog";
import { UserDialog } from "@/components/users/user-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function StatusBadge({ user }: { user: User }) {
  // Invited is a third state derived from one column: invited_at IS NOT NULL.
  if (user.invited_at) {
    return (
      <Badge variant="outline">
        <span className="size-1.5 rounded-full bg-primary" aria-hidden />
        Invited
      </Badge>
    );
  }
  return (
    <Badge variant={user.is_active ? "success" : "muted"}>
      <span
        className={`size-1.5 rounded-full ${user.is_active ? "bg-success" : "bg-muted-foreground"}`}
        aria-hidden
      />
      {user.is_active ? "Active" : "Inactive"}
    </Badge>
  );
}

function LoadingRows({ cols }: { cols: number }) {
  return (
    <>
      {[0, 1, 2].map((i) => (
        <TableRow key={i}>
          {Array.from({ length: cols }).map((_, c) => (
            <TableCell key={c}>
              <Skeleton className="h-4 w-full" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function UsersView() {
  const { user: currentUser } = useAuth();
  const [rows, setRows] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [userDialog, setUserDialog] = useState<{ open: boolean; user: User | null }>({
    open: false,
    user: null,
  });
  const [resetTarget, setResetTarget] = useState<User | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null);
  const [totpTarget, setTotpTarget] = useState<User | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteKey, setInviteKey] = useState(0);
  // Hidden until the backend says an invitation could actually be sent.
  const [canInvite, setCanInvite] = useState(false);

  // `load` performs no synchronous setState, so it is safe to call from an
  // effect body; `refresh` (event handlers) shows the skeleton while reloading.
  const load = useCallback(async () => {
    try {
      setRows(await users.list());
      setLoadError(null);
    } catch (err) {
      setLoadError(describeError(err).message);
    } finally {
      setLoading(false);
    }
  }, []);

  const refresh = useCallback(() => {
    setLoading(true);
    void load();
  }, [load]);

  useEffect(() => {
    let active = true;
    void (async () => {
      if (active) await load();
    })();
    return () => {
      active = false;
    };
  }, [load]);

  useEffect(() => {
    let active = true;
    fetchCapabilities()
      .then((caps) => {
        if (active) setCanInvite(caps.password_reset);
      })
      .catch(() => {
        // Leave the button hidden; the list load reports the real error.
      });
    return () => {
      active = false;
    };
  }, []);

  async function resend(u: User) {
    try {
      await users.resendInvite(u.id);
      toast.success(`Invitation resent to ${u.email}`);
    } catch (err) {
      toast.error(describeError(err).message);
    }
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <UsersIcon className="size-5" />
        </div>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Users</h2>
          <p className="text-sm text-muted-foreground">
            Accounts and roles for people who sign in to MegooPM.
          </p>
        </div>
      </div>

      {loadError ? (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-destructive" role="alert">
            Couldn’t load users: {loadError}
          </p>
          <Button variant="outline" size="sm" onClick={refresh}>
            Retry
          </Button>
        </div>
      ) : null}

      <div className="space-y-3">
        <div className="flex justify-end gap-2">
          {canInvite ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setInviteKey((k) => k + 1);
                setInviteOpen(true);
              }}
            >
              <MailPlus /> Invite user
            </Button>
          ) : null}
          <Button size="sm" onClick={() => setUserDialog({ open: true, user: null })}>
            <Plus /> New user
          </Button>
        </div>
        <div className="bg-card text-card-foreground rounded-xl border shadow-xs">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>2FA</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="w-32 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <LoadingRows cols={6} />
              ) : rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
                    No users yet.
                  </TableCell>
                </TableRow>
              ) : (
                rows.map((u) => {
                  const self = isSelf(u, currentUser);
                  return (
                    <TableRow key={u.id}>
                      <TableCell className="font-medium">
                        <span className="inline-flex items-center gap-2">
                          {displayName(u)}
                          {self ? <Badge variant="outline">You</Badge> : null}
                        </span>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{u.email}</TableCell>
                      <TableCell>
                        <Badge variant={u.role === "admin" ? "default" : "muted"}>
                          {USER_ROLE_LABELS[u.role]}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <StatusBadge user={u} />
                      </TableCell>
                      <TableCell>
                        <Badge variant={u.totp_enabled ? "success" : "muted"}>
                          {u.totp_enabled ? "On" : "Off"}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {formatDate(u.created_at)}
                      </TableCell>
                      <TableCell>
                        <div className="flex justify-end gap-1">
                          {u.totp_enabled ? (
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={`Disable 2FA for ${u.email}`}
                              onClick={() => setTotpTarget(u)}
                            >
                              <ShieldOff />
                            </Button>
                          ) : null}
                          {u.invited_at ? (
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={`Resend invitation to ${u.email}`}
                              onClick={() => void resend(u)}
                            >
                              <Send />
                            </Button>
                          ) : null}
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={`Edit ${u.email}`}
                            onClick={() => setUserDialog({ open: true, user: u })}
                          >
                            <Pencil />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={`Reset password for ${u.email}`}
                            onClick={() => setResetTarget(u)}
                          >
                            <KeyRound />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={`Delete ${u.email}`}
                            disabled={self}
                            title={self ? "You cannot delete your own account." : undefined}
                            onClick={() => setDeleteTarget(u)}
                          >
                            <Trash2 />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      {/* `key` remounts each dialog per target so its form starts fresh
          (the same pattern proxy-hosts-view uses). */}
      <UserDialog
        key={userDialog.user?.id ?? "new-user"}
        open={userDialog.open}
        onOpenChange={(open) => setUserDialog((s) => ({ ...s, open }))}
        user={userDialog.user}
        currentUser={currentUser}
        onSaved={refresh}
      />
      <InviteDialog
        key={`invite-${inviteKey}`}
        open={inviteOpen}
        onOpenChange={setInviteOpen}
        onSaved={refresh}
      />
      <ResetPasswordDialog
        key={resetTarget?.id ?? "no-reset"}
        open={resetTarget !== null}
        onOpenChange={(open) => {
          if (!open) setResetTarget(null);
        }}
        user={resetTarget}
        onSaved={refresh}
      />
      <ConfirmDeleteDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title={deleteTarget?.invited_at ? "Withdraw invitation" : "Delete user"}
        description={
          deleteTarget
            ? deleteTarget.invited_at
              ? `Withdraw the invitation to ${deleteTarget.email}? Their link will stop working. You can invite them again later.`
              : `Delete ${displayName(deleteTarget)} (${deleteTarget.email})? They will be signed out immediately. This cannot be undone.`
            : ""
        }
        onConfirm={async () => {
          if (deleteTarget) await users.remove(deleteTarget.id);
        }}
        onDeleted={refresh}
      />
      {/* The generic confirm-with-a-destructive-button is exactly the shape
          this needs; the copy is what makes it a different act. */}
      <ConfirmDeleteDialog
        open={totpTarget !== null}
        onOpenChange={(open) => {
          if (!open) setTotpTarget(null);
        }}
        title="Disable two-factor authentication?"
        description={
          totpTarget
            ? `Turn off two-factor authentication for ${displayName(totpTarget)} (${totpTarget.email})? They will be signed out everywhere and emailed that you did this. Use this only when they have lost their authenticator and their recovery codes.`
            : ""
        }
        onConfirm={async () => {
          if (totpTarget) await users.adminTotpDisable(totpTarget.id);
        }}
        onDeleted={refresh}
      />
    </div>
  );
}
