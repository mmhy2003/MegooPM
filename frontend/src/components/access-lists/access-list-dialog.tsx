"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  ACCESS_LIST_DIRECTIVES,
  DIRECTIVE_LABELS,
  accessLists,
  type AccessList,
  type AccessListDirective,
} from "@/lib/api";
import {
  blankClientRow,
  blankUserRow,
  buildCreatePayload,
  buildUpdatePayload,
  describeError,
  satisfyDescription,
  stateFromList,
  validateAccessListForm,
  type AccessListFormState,
  type AccessListTab,
  type AuthUserRow,
  type ClientRow,
} from "@/components/access-lists/lib";
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
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";

/**
 * Create or edit an access list in one form.
 *
 * Details, basic-auth users and IP rules live on three tabs and save together:
 * one request, so the backend performs one transaction and one nginx reload
 * rather than one per user and rule. Passing `list` switches the dialog to edit
 * mode; the parent should key it by list id so reopening reseeds the form.
 */
export function AccessListDialog({
  open,
  onOpenChange,
  list,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  list?: AccessList | null;
  onSaved: (list: AccessList) => void;
}) {
  const editing = Boolean(list);
  const [form, setForm] = useState<AccessListFormState>(() => stateFromList(list));
  const [tab, setTab] = useState<AccessListTab>("details");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function patch(changes: Partial<AccessListFormState>) {
    setForm((current) => ({ ...current, ...changes }));
  }

  async function handleSubmit() {
    const problem = validateAccessListForm(form);
    if (problem) {
      // Reveal the panel holding the bad field — an error about a control the
      // user cannot see is not actionable.
      setTab(problem.tab);
      setError(problem.message);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const saved = list
        ? await accessLists.update(list.id, buildUpdatePayload(form))
        : await accessLists.create(buildCreatePayload(form));
      toast.success(editing ? "Access list saved" : "Access list created");
      onOpenChange(false);
      onSaved(saved);
    } catch (err) {
      // 409 → duplicate username; 422 → invalid IP/CIDR or a missing password.
      const described = describeError(err);
      setError(described.message);
      toast.error(described.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{editing ? "Edit access list" : "New access list"}</DialogTitle>
          <DialogDescription>
            An authorization gate you can attach to one or more proxy hosts. Basic-auth users and IP
            rules are saved together with the settings.
          </DialogDescription>
        </DialogHeader>

        <Tabs value={tab} onValueChange={(value) => setTab(value as AccessListTab)}>
          <TabsList>
            <TabsTab value="details">Details</TabsTab>
            <TabsTab value="authorization">Authorization</TabsTab>
            <TabsTab value="access">Access</TabsTab>
          </TabsList>

          <TabsPanel value="details" className="space-y-4 pt-2">
            <DetailsPanel form={form} disabled={saving} onChange={patch} />
          </TabsPanel>

          <TabsPanel value="authorization" className="space-y-3 pt-2">
            <AuthUsersPanel
              rows={form.users}
              disabled={saving}
              onChange={(users) => patch({ users })}
            />
          </TabsPanel>

          <TabsPanel value="access" className="space-y-3 pt-2">
            <ClientRulesPanel
              rows={form.clients}
              disabled={saving}
              onChange={(clients) => patch({ clients })}
            />
          </TabsPanel>
        </Tabs>

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
            {saving ? "Saving…" : editing ? "Save changes" : "Create list"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* -------------------------------------------------------------------------- */
/* Details                                                                     */
/* -------------------------------------------------------------------------- */

function DetailsPanel({
  form,
  disabled,
  onChange,
}: {
  form: AccessListFormState;
  disabled: boolean;
  onChange: (changes: Partial<AccessListFormState>) => void;
}) {
  return (
    <>
      <div className="space-y-1.5">
        <Label htmlFor="al-name">Name</Label>
        <Input
          id="al-name"
          value={form.name}
          onChange={(e) => onChange({ name: e.target.value })}
          placeholder="Internal only"
          disabled={disabled}
          autoFocus
        />
      </div>

      <label className="flex items-start gap-2">
        <Switch
          aria-label="Satisfy Any"
          checked={form.satisfyAny}
          onCheckedChange={(satisfyAny) => onChange({ satisfyAny })}
          disabled={disabled}
        />
        <span className="space-y-0.5">
          <span className="block text-sm font-medium leading-none">Satisfy Any</span>
          <span className="block text-xs text-muted-foreground">
            {satisfyDescription(form.satisfyAny)}
          </span>
        </span>
      </label>

      <label className="flex items-start gap-2">
        <Switch
          aria-label="Pass Auth"
          checked={form.passAuth}
          onCheckedChange={(passAuth) => onChange({ passAuth })}
          disabled={disabled}
        />
        <span className="space-y-0.5">
          <span className="block text-sm font-medium leading-none">Pass Auth</span>
          <span className="block text-xs text-muted-foreground">
            Forward the Authorization header to the upstream instead of stripping it.
          </span>
        </span>
      </label>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Authorization                                                               */
/* -------------------------------------------------------------------------- */

function AuthUsersPanel({
  rows,
  disabled,
  onChange,
}: {
  rows: AuthUserRow[];
  disabled: boolean;
  onChange: (rows: AuthUserRow[]) => void;
}) {
  function update(index: number, changes: Partial<AuthUserRow>) {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...changes } : row)));
  }

  return (
    <>
      <p className="text-xs text-muted-foreground">
        HTTP basic-auth credentials. With no users the basic-auth gate lets everyone through.
      </p>

      <div className="space-y-2">
        <div className="hidden gap-2 px-1 text-xs font-medium text-muted-foreground sm:flex">
          <span className="flex-1">Username</span>
          <span className="flex-1">Password</span>
          <span className="w-8" />
        </div>

        {rows.map((row, index) => (
          <div key={row.id ?? `new-${index}`} className="flex flex-col gap-2 sm:flex-row">
            <Input
              aria-label="Username"
              className="flex-1"
              value={row.username}
              onChange={(e) => update(index, { username: e.target.value })}
              placeholder="alice"
              disabled={disabled}
            />
            <Input
              aria-label="Password"
              className="flex-1"
              type="password"
              value={row.password}
              onChange={(e) => update(index, { password: e.target.value })}
              // An existing user's stored hash is never returned, so a blank
              // field means "leave it alone" rather than "no password".
              placeholder={row.id === undefined ? "••••••••" : "unchanged"}
              disabled={disabled}
            />
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={`Remove user ${index + 1}`}
              title="Remove"
              disabled={disabled}
              onClick={() => onChange(rows.filter((_, i) => i !== index))}
            >
              <Trash2 />
            </Button>
          </div>
        ))}
      </div>

      <Button
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={() => onChange([...rows, blankUserRow()])}
      >
        <Plus /> Add user
      </Button>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Access                                                                      */
/* -------------------------------------------------------------------------- */

function ClientRulesPanel({
  rows,
  disabled,
  onChange,
}: {
  rows: ClientRow[];
  disabled: boolean;
  onChange: (rows: ClientRow[]) => void;
}) {
  function update(index: number, changes: Partial<ClientRow>) {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...changes } : row)));
  }

  return (
    <>
      <p className="text-xs text-muted-foreground">
        Allow/deny rules for an IP, a CIDR range, or “all”. With no rules the IP gate lets everyone
        through.
      </p>

      <div className="space-y-2">
        <div className="hidden gap-2 px-1 text-xs font-medium text-muted-foreground sm:flex">
          <span className="w-28">Directive</span>
          <span className="flex-1">Address</span>
          <span className="w-8" />
        </div>

        {rows.map((row, index) => (
          <div key={row.id ?? `new-${index}`} className="flex flex-col gap-2 sm:flex-row">
            <Select
              value={row.directive}
              onValueChange={(v) => update(index, { directive: v as AccessListDirective })}
              items={DIRECTIVE_LABELS}
            >
              <SelectTrigger aria-label="Directive" className="w-28" disabled={disabled}>
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
            <Input
              aria-label="Address"
              className="flex-1 font-mono text-sm"
              value={row.address}
              onChange={(e) => update(index, { address: e.target.value })}
              placeholder="10.0.0.0/8, 203.0.113.4, or all"
              disabled={disabled}
            />
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={`Remove rule ${index + 1}`}
              title="Remove"
              disabled={disabled}
              onClick={() => onChange(rows.filter((_, i) => i !== index))}
            >
              <Trash2 />
            </Button>
          </div>
        ))}
      </div>

      <Button
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={() => onChange([...rows, blankClientRow()])}
      >
        <Plus /> Add rule
      </Button>
    </>
  );
}
