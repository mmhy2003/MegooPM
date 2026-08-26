"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyRound, Loader2, Plus, Trash2, UserPlus, X } from "lucide-react";
import { toast } from "sonner";

import {
  ACCESS_LIST_DIRECTIVES,
  DIRECTIVE_LABELS,
  accessLists,
  type AccessList,
  type AccessListAuthUser,
  type AccessListClientRule,
  type AccessListDirective,
} from "@/lib/api";
import {
  describeError,
  normalizeAddress,
  satisfyDescription,
} from "@/components/access-lists/lib";
import { Badge } from "@/components/ui/badge";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/**
 * Full editor for a single access list: rename + gate toggles, basic-auth user
 * management (add / reset password / remove) and allow/deny IP rules
 * (add / edit / remove). Each collection mutation hits its dedicated endpoint
 * and reloads the list; `onChanged` refreshes the parent index so counts stay
 * in sync.
 */
export function AccessListEditor({
  open,
  onOpenChange,
  listId,
  onChanged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  listId: number;
  onChanged: () => void;
}) {
  const [list, setList] = useState<AccessList | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const detail = await accessLists.get(listId);
      setList(detail);
      setLoadError(null);
    } catch (err) {
      setLoadError(describeError(err).message);
    }
  }, [listId]);

  // `reload` awaits before any setState, so it never updates state synchronously
  // in the effect body; the IIFE keeps the effect callback itself sync.
  useEffect(() => {
    void (async () => {
      await reload();
    })();
  }, [reload]);

  // Notify the parent + reload local detail after a collection mutation.
  const afterMutation = useCallback(async () => {
    await reload();
    onChanged();
  }, [reload, onChanged]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Edit access list</DialogTitle>
          <DialogDescription>
            Manage the gate settings, basic-auth users and IP allow/deny rules
            for this list.
          </DialogDescription>
        </DialogHeader>

        {loadError ? (
          <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
            <p className="text-sm text-destructive" role="alert">
              Couldn&apos;t load this access list: {loadError}
            </p>
            <Button variant="outline" size="sm" onClick={() => void reload()}>
              Retry
            </Button>
          </div>
        ) : !list ? (
          <div className="space-y-3">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <div className="space-y-6">
            <SettingsSection list={list} onSaved={afterMutation} />
            <AuthUsersSection list={list} onChanged={afterMutation} />
            <ClientRulesSection list={list} onChanged={afterMutation} />
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* -------------------------------------------------------------------------- */
/* Settings                                                                    */
/* -------------------------------------------------------------------------- */

function SettingsSection({
  list,
  onSaved,
}: {
  list: AccessList;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState(list.name);
  const [satisfyAny, setSatisfyAny] = useState(list.satisfy_any ?? false);
  const [passAuth, setPassAuth] = useState(list.pass_auth ?? false);
  const [saving, setSaving] = useState(false);

  const dirty =
    name.trim() !== list.name ||
    satisfyAny !== (list.satisfy_any ?? false) ||
    passAuth !== (list.pass_auth ?? false);

  async function handleSave() {
    if (!name.trim()) {
      toast.error("Name can't be empty.");
      return;
    }
    setSaving(true);
    try {
      await accessLists.update(list.id, {
        name: name.trim(),
        satisfy_any: satisfyAny,
        pass_auth: passAuth,
      });
      toast.success("Settings saved");
      await onSaved();
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="space-y-4">
      <h3 className="text-sm font-semibold">Settings</h3>
      <div className="space-y-1.5">
        <Label htmlFor="al-edit-name">Name</Label>
        <Input
          id="al-edit-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={saving}
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
            Forward the Authorization header to the upstream.
          </span>
        </span>
      </label>
      <div className="flex justify-end">
        <Button size="sm" onClick={handleSave} disabled={saving || !dirty}>
          {saving ? "Saving…" : "Save settings"}
        </Button>
      </div>
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* Auth users                                                                  */
/* -------------------------------------------------------------------------- */

function AuthUsersSection({
  list,
  onChanged,
}: {
  list: AccessList;
  onChanged: () => Promise<void>;
}) {
  const users = list.auth_users ?? [];
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [adding, setAdding] = useState(false);
  const [removingId, setRemovingId] = useState<number | null>(null);
  const [resetTarget, setResetTarget] = useState<AccessListAuthUser | null>(null);

  async function handleAdd() {
    if (!username.trim() || !password) {
      toast.error("Enter a username and password.");
      return;
    }
    setAdding(true);
    try {
      await accessLists.authUsers.add(list.id, {
        username: username.trim(),
        password,
      });
      toast.success(`Added “${username.trim()}”`);
      setUsername("");
      setPassword("");
      await onChanged();
    } catch (err) {
      // 409 → duplicate username; describeError surfaces the backend detail.
      toast.error(describeError(err).message);
    } finally {
      setAdding(false);
    }
  }

  async function handleRemove(user: AccessListAuthUser) {
    setRemovingId(user.id);
    try {
      await accessLists.authUsers.remove(list.id, user.id);
      toast.success(`Removed “${user.username}”`);
      await onChanged();
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setRemovingId(null);
    }
  }

  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold">
        Basic-auth users{" "}
        <span className="text-muted-foreground font-normal">
          ({users.length})
        </span>
      </h3>

      <div className="rounded-xl border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Username</TableHead>
              <TableHead className="w-24 text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {users.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={2}
                  className="py-6 text-center text-sm text-muted-foreground"
                >
                  No users yet. Anyone clears the basic-auth gate until you add
                  one.
                </TableCell>
              </TableRow>
            ) : (
              users.map((user) => (
                <TableRow key={user.id}>
                  <TableCell className="font-medium">{user.username}</TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Reset password for ${user.username}`}
                        title="Reset password"
                        onClick={() => setResetTarget(user)}
                      >
                        <KeyRound />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Remove ${user.username}`}
                        title="Remove"
                        disabled={removingId === user.id}
                        onClick={() => handleRemove(user)}
                      >
                        {removingId === user.id ? (
                          <Loader2 className="animate-spin" />
                        ) : (
                          <Trash2 />
                        )}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="flex-1 space-y-1.5">
          <Label htmlFor="al-new-username">Username</Label>
          <Input
            id="al-new-username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="alice"
            disabled={adding}
          />
        </div>
        <div className="flex-1 space-y-1.5">
          <Label htmlFor="al-new-password">Password</Label>
          <Input
            id="al-new-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            disabled={adding}
          />
        </div>
        <Button size="sm" onClick={handleAdd} disabled={adding}>
          <UserPlus /> Add user
        </Button>
      </div>

      {resetTarget ? (
        <ResetPasswordDialog
          listId={list.id}
          user={resetTarget}
          onOpenChange={(o) => !o && setResetTarget(null)}
          onDone={onChanged}
        />
      ) : null}
    </section>
  );
}

function ResetPasswordDialog({
  listId,
  user,
  onOpenChange,
  onDone,
}: {
  listId: number;
  user: AccessListAuthUser;
  onOpenChange: (open: boolean) => void;
  onDone: () => Promise<void>;
}) {
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!password) {
      toast.error("Enter a new password.");
      return;
    }
    setSaving(true);
    try {
      await accessLists.authUsers.resetPassword(listId, user.id, { password });
      toast.success(`Password reset for “${user.username}”`);
      onOpenChange(false);
      await onDone();
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Reset password</DialogTitle>
          <DialogDescription>
            Set a new password for <strong>{user.username}</strong>. The current
            one is never shown and will be replaced.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label htmlFor="al-reset-password">New password</Label>
          <Input
            id="al-reset-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={saving}
            autoFocus
          />
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Reset password"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* -------------------------------------------------------------------------- */
/* Client (IP) rules                                                           */
/* -------------------------------------------------------------------------- */

function ClientRulesSection({
  list,
  onChanged,
}: {
  list: AccessList;
  onChanged: () => Promise<void>;
}) {
  const rules = list.client_rules ?? [];
  const [directive, setDirective] = useState<AccessListDirective>("allow");
  const [address, setAddress] = useState("");
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  async function handleAdd() {
    const addr = normalizeAddress(address);
    if (!addr) {
      toast.error("Enter an IP, CIDR, or “all”.");
      return;
    }
    setAdding(true);
    try {
      await accessLists.clients.add(list.id, { address: addr, directive });
      toast.success(`${DIRECTIVE_LABELS[directive]} ${addr}`);
      setAddress("");
      setDirective("allow");
      await onChanged();
    } catch (err) {
      // 422 → invalid IP/CIDR; describeError surfaces the field message.
      toast.error(describeError(err).message);
    } finally {
      setAdding(false);
    }
  }

  async function handleRemove(rule: AccessListClientRule) {
    setBusyId(rule.id);
    try {
      await accessLists.clients.remove(list.id, rule.id);
      toast.success("Rule removed");
      await onChanged();
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold">
        IP rules{" "}
        <span className="text-muted-foreground font-normal">
          ({rules.length})
        </span>
      </h3>

      <div className="rounded-xl border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-28">Directive</TableHead>
              <TableHead>Address</TableHead>
              <TableHead className="w-24 text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rules.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={3}
                  className="py-6 text-center text-sm text-muted-foreground"
                >
                  No IP rules. The IP gate passes everyone until you add one.
                </TableCell>
              </TableRow>
            ) : null}
            {rules.map((rule) =>
              editingId === rule.id ? (
                <EditRuleRow
                  key={rule.id}
                  listId={list.id}
                  rule={rule}
                  onCancel={() => setEditingId(null)}
                  onSaved={async () => {
                    setEditingId(null);
                    await onChanged();
                  }}
                />
              ) : (
                <TableRow key={rule.id}>
                  <TableCell>
                    <Badge
                      variant={rule.directive === "allow" ? "success" : "destructive"}
                    >
                      {DIRECTIVE_LABELS[rule.directive]}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-sm">
                    {rule.address}
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditingId(rule.id)}
                        disabled={busyId === rule.id}
                      >
                        Edit
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Remove rule ${rule.address}`}
                        title="Remove"
                        disabled={busyId === rule.id}
                        onClick={() => handleRemove(rule)}
                      >
                        {busyId === rule.id ? (
                          <Loader2 className="animate-spin" />
                        ) : (
                          <Trash2 />
                        )}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ),
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="space-y-1.5">
          <Label htmlFor="al-new-directive">Directive</Label>
          <Select
            value={directive}
            onValueChange={(v) => setDirective(v as AccessListDirective)}
          >
            <SelectTrigger id="al-new-directive" className="w-28" disabled={adding}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ACCESS_LIST_DIRECTIVES.map((d) => (
                <SelectItem key={d} value={d}>
                  {DIRECTIVE_LABELS[d]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex-1 space-y-1.5">
          <Label htmlFor="al-new-address">Address</Label>
          <Input
            id="al-new-address"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="10.0.0.0/8, 203.0.113.4, or all"
            className="font-mono text-sm"
            disabled={adding}
          />
        </div>
        <Button size="sm" onClick={handleAdd} disabled={adding}>
          <Plus /> Add rule
        </Button>
      </div>
    </section>
  );
}

function EditRuleRow({
  listId,
  rule,
  onCancel,
  onSaved,
}: {
  listId: number;
  rule: AccessListClientRule;
  onCancel: () => void;
  onSaved: () => Promise<void>;
}) {
  const [directive, setDirective] = useState<AccessListDirective>(rule.directive);
  const [address, setAddress] = useState(rule.address);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    const addr = normalizeAddress(address);
    if (!addr) {
      toast.error("Enter an IP, CIDR, or “all”.");
      return;
    }
    setSaving(true);
    try {
      await accessLists.clients.update(listId, rule.id, {
        address: addr,
        directive,
      });
      toast.success("Rule updated");
      await onSaved();
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <TableRow>
      <TableCell>
        <Select
          value={directive}
          onValueChange={(v) => setDirective(v as AccessListDirective)}
        >
          <SelectTrigger
            aria-label="Directive"
            className="w-24"
            disabled={saving}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ACCESS_LIST_DIRECTIVES.map((d) => (
              <SelectItem key={d} value={d}>
                {DIRECTIVE_LABELS[d]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </TableCell>
      <TableCell>
        <Input
          aria-label="Address"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          className="font-mono text-sm"
          disabled={saving}
        />
      </TableCell>
      <TableCell>
        <div className="flex justify-end gap-1">
          <Button size="sm" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Cancel edit"
            title="Cancel"
            onClick={onCancel}
            disabled={saving}
          >
            <X />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}
