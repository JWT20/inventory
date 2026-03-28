import { useState, useEffect, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Trash2, KeyRound } from "lucide-react";

interface User {
  id: number;
  username: string;
  role: string;
  is_platform_admin: boolean;
  organization_id: number | null;
  organization_name: string | null;
  is_active: boolean;
  created_at: string;
}

interface Organization {
  id: number;
  name: string;
  slug: string;
  enabled_modules: string[];
  created_at: string;
}

const ROLE_LABELS: Record<string, string> = {
  owner: "Eigenaar",
  member: "Medewerker",
  courier: "Koerier",
  customer: "Klant",
};

export function AccountsPage() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [showNew, setShowNew] = useState(false);
  const [showNewOrg, setShowNewOrg] = useState(false);
  const [resetUser, setResetUser] = useState<User | null>(null);

  const load = useCallback(async () => {
    try {
      const [u, o] = await Promise.all([
        api.listUsers(),
        api.listOrganizations(),
      ]);
      setUsers(u);
      setOrganizations(o);
    } catch {
      toast.error("Kan gegevens niet laden");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDeleteUser(u: User) {
    if (u.id === me?.id) {
      toast.error("Je kan jezelf niet verwijderen");
      return;
    }
    if (!confirm(`Gebruiker '${u.username}' verwijderen?`)) return;
    try {
      await api.deleteUser(u.id);
      toast.success("Gebruiker verwijderd");
      load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    }
  }

  async function handleDeleteOrg(org: Organization) {
    if (!confirm(`Organisatie '${org.name}' verwijderen?`)) return;
    try {
      await api.deleteOrganization(org.id);
      toast.success("Organisatie verwijderd");
      load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    }
  }

  return (
    <>
      {/* Organizations section */}
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Organisaties</h2>
        <Button size="sm" onClick={() => setShowNewOrg(true)}>
          + Organisatie
        </Button>
      </div>

      <div className="space-y-3 mb-8">
        {organizations.map((org) => (
          <Card key={org.id} className="p-4">
            <div className="flex justify-between items-center">
              <div>
                <p className="font-semibold">{org.name}</p>
                <p className="text-xs text-muted-foreground">{org.slug}</p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => handleDeleteOrg(org)}
              >
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </div>
          </Card>
        ))}
        {organizations.length === 0 && (
          <p className="text-center text-muted-foreground py-4">
            Geen organisaties
          </p>
        )}
      </div>

      {/* Users section */}
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Gebruikers</h2>
        <Button size="sm" onClick={() => setShowNew(true)}>
          + Gebruiker
        </Button>
      </div>

      <div className="space-y-3">
        {users.map((u) => (
          <Card key={u.id} className="p-4">
            <div className="flex justify-between items-center">
              <div>
                <p className="font-semibold">{u.username}</p>
                <div className="flex gap-2 mt-1 flex-wrap">
                  {u.is_platform_admin && (
                    <Badge variant="default">Platform Admin</Badge>
                  )}
                  <Badge variant="secondary">
                    {ROLE_LABELS[u.role] || u.role}
                  </Badge>
                  {u.organization_name && (
                    <Badge variant="outline">{u.organization_name}</Badge>
                  )}
                </div>
              </div>
              <div className="flex gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setResetUser(u)}
                  title="Wachtwoord resetten"
                >
                  <KeyRound className="h-4 w-4" />
                </Button>
                {u.id !== me?.id && (
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => handleDeleteUser(u)}
                  >
                    <Trash2 className="h-4 w-4 text-destructive" />
                  </Button>
                )}
              </div>
            </div>
          </Card>
        ))}
      </div>

      <NewUserDialog
        open={showNew}
        onClose={() => setShowNew(false)}
        onCreated={load}
        organizations={organizations}
      />

      <NewOrgDialog
        open={showNewOrg}
        onClose={() => setShowNewOrg(false)}
        onCreated={load}
      />

      <ResetPasswordDialog
        user={resetUser}
        onClose={() => setResetUser(null)}
      />
    </>
  );
}

function NewUserDialog({
  open,
  onClose,
  onCreated,
  organizations,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  organizations: { id: number; name: string }[];
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("courier");
  const [orgId, setOrgId] = useState<number | "">("");

  useEffect(() => {
    if (open) {
      setUsername("");
      setPassword("");
      setRole("courier");
      setOrgId("");
    }
  }, [open]);

  const needsOrg = role === "owner" || role === "member" || role === "customer";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.createUser({
        username,
        password,
        role,
        organization_id: needsOrg ? (orgId as number) : null,
      });
      toast.success(`Gebruiker '${username}' aangemaakt`);
      onClose();
      onCreated();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nieuwe gebruiker</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label>Gebruikersnaam</Label>
            <Input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              minLength={3}
              required
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <Label>Wachtwoord</Label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
              required
            />
            <p className="text-xs text-muted-foreground">
              Min. 8 tekens, 1 hoofdletter, 1 kleine letter, 1 cijfer
            </p>
          </div>
          <div className="space-y-2">
            <Label>Rol</Label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              <option value="courier">Koerier</option>
              <option value="owner">Eigenaar (organisatie)</option>
              <option value="member">Medewerker (organisatie)</option>
              <option value="customer">Klant (organisatie)</option>
            </select>
          </div>
          {needsOrg && (
            <div className="space-y-2">
              <Label>Organisatie</Label>
              <select
                value={orgId}
                onChange={(e) =>
                  setOrgId(e.target.value ? Number(e.target.value) : "")
                }
                required
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                <option value="">Selecteer organisatie...</option>
                {organizations.map((org) => (
                  <option key={org.id} value={org.id}>
                    {org.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          <Button type="submit" className="w-full">
            Aanmaken
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function NewOrgDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");

  useEffect(() => {
    if (open) {
      setName("");
      setSlug("");
    }
  }, [open]);

  function handleNameChange(v: string) {
    setName(v);
    // Auto-generate slug from name
    setSlug(
      v
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, ""),
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.createOrganization({ name, slug });
      toast.success(`Organisatie '${name}' aangemaakt`);
      onClose();
      onCreated();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nieuwe organisatie</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label>Naam</Label>
            <Input
              value={name}
              onChange={(e) => handleNameChange(e.target.value)}
              minLength={1}
              required
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <Label>Slug</Label>
            <Input
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              minLength={1}
              required
            />
            <p className="text-xs text-muted-foreground">
              Unieke identifier (bijv. "de-druif")
            </p>
          </div>
          <Button type="submit" className="w-full">
            Aanmaken
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ResetPasswordDialog({
  user,
  onClose,
}: {
  user: User | null;
  onClose: () => void;
}) {
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (user) setPassword("");
  }, [user]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!user) return;
    try {
      await api.resetUserPassword(user.id, password);
      toast.success(`Wachtwoord voor '${user.username}' gewijzigd`);
      onClose();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    }
  }

  return (
    <Dialog open={!!user} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Wachtwoord resetten: {user?.username}</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label>Nieuw wachtwoord</Label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
              required
              autoFocus
            />
            <p className="text-xs text-muted-foreground">
              Min. 8 tekens, 1 hoofdletter, 1 kleine letter, 1 cijfer
            </p>
          </div>
          <Button type="submit" className="w-full">
            Wachtwoord opslaan
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
