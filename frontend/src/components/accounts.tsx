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
import { Trash2, KeyRound, Pencil } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

interface User {
  id: number;
  username: string;
  role: string;
  is_platform_admin: boolean;
  organization_id: number | null;
  organization_name: string | null;
  customer_id: number | null;
  customer_name: string | null;
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

interface Customer {
  id: number;
  name: string;
  show_prices: boolean;
  sku_ids: number[];
  created_at: string;
}

interface Supplier {
  id: number;
  name: string;
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
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [showNew, setShowNew] = useState(false);
  const [showNewOrg, setShowNewOrg] = useState(false);
  const [showNewCustomer, setShowNewCustomer] = useState(false);
  const [showNewSupplier, setShowNewSupplier] = useState(false);
  const [editCustomer, setEditCustomer] = useState<Customer | null>(null);
  const [resetUser, setResetUser] = useState<User | null>(null);

  const load = useCallback(async () => {
    try {
      const customerPromise = api.listCustomers();
      const supplierPromise = api.listSuppliers();
      if (me?.is_platform_admin) {
        const [u, o, c, s] = await Promise.all([
          api.listUsers(),
          api.listOrganizations(),
          customerPromise,
          supplierPromise,
        ]);
        setUsers(u);
        setOrganizations(o);
        setCustomers(c);
        setSuppliers(s);
      } else {
        const [c, s] = await Promise.all([customerPromise, supplierPromise]);
        setCustomers(c);
        setSuppliers(s);
      }
    } catch {
      toast.error("Kan gegevens niet laden");
    }
  }, [me]);

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

  async function handleDeleteCustomer(c: Customer) {
    if (!confirm(`Klant '${c.name}' verwijderen?`)) return;
    try {
      await api.deleteCustomer(c.id);
      toast.success("Klant verwijderd");
      load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    }
  }

  async function handleDeleteSupplier(s: Supplier) {
    if (!confirm(`Leverancier '${s.name}' verwijderen?`)) return;
    try {
      await api.deleteSupplier(s.id);
      toast.success("Leverancier verwijderd");
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
      {/* Organizations section - admin only */}
      {me?.is_platform_admin && (
      <>
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
      </>
      )}

      {/* Customers section */}
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Klanten</h2>
        <Button size="sm" onClick={() => setShowNewCustomer(true)}>
          + Klant
        </Button>
      </div>

      <div className="space-y-3 mb-8">
        {customers.map((c) => (
          <Card key={c.id} className="p-4">
            <div className="flex justify-between items-center">
              <div>
                <p className="font-semibold">{c.name}</p>
                <div className="flex gap-2 mt-1">
                  <Badge variant={c.show_prices ? "default" : "secondary"}>
                    {c.show_prices ? "Prijzen zichtbaar" : "Prijzen verborgen"}
                  </Badge>
                  <Badge variant="outline">
                    {c.sku_ids.length} producten
                  </Badge>
                </div>
              </div>
              <div className="flex gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setEditCustomer(c)}
                  title="Bewerken"
                >
                  <Pencil className="h-4 w-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => handleDeleteCustomer(c)}
                >
                  <Trash2 className="h-4 w-4 text-destructive" />
                </Button>
              </div>
            </div>
          </Card>
        ))}
        {customers.length === 0 && (
          <p className="text-center text-muted-foreground py-4">
            Geen klanten
          </p>
        )}
      </div>

      {/* Suppliers section */}
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Leveranciers</h2>
        <Button size="sm" onClick={() => setShowNewSupplier(true)}>
          + Leverancier
        </Button>
      </div>

      <div className="space-y-3 mb-8">
        {suppliers.map((s) => (
          <Card key={s.id} className="p-4">
            <div className="flex justify-between items-center">
              <p className="font-semibold">{s.name}</p>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => handleDeleteSupplier(s)}
              >
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </div>
          </Card>
        ))}
        {suppliers.length === 0 && (
          <p className="text-center text-muted-foreground py-4">
            Geen leveranciers
          </p>
        )}
      </div>

      {/* Users section - admin only */}
      {me?.is_platform_admin && (
      <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Gebruikers</h2>
        <Button size="sm" onClick={() => setShowNew(true)}>
          + Gebruiker
        </Button>
      </div>

      <div className="space-y-3 mb-8">
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
                  {u.customer_name && (
                    <Badge variant="outline">Klant: {u.customer_name}</Badge>
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
      </>
      )}

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

      <NewCustomerDialog
        open={showNewCustomer}
        onClose={() => setShowNewCustomer(false)}
        onCreated={load}
        organizations={organizations}
      />

      <EditCustomerDialog
        customer={editCustomer}
        onClose={() => setEditCustomer(null)}
        onUpdated={load}
      />

      <ResetPasswordDialog
        user={resetUser}
        onClose={() => setResetUser(null)}
      />

      <NewSupplierDialog
        open={showNewSupplier}
        onClose={() => setShowNewSupplier(false)}
        onCreated={load}
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
  const [customerId, setCustomerId] = useState<number | "">("");
  const [customers, setCustomers] = useState<{ id: number; name: string }[]>([]);

  useEffect(() => {
    if (open) {
      setUsername("");
      setPassword("");
      setRole("courier");
      setOrgId("");
      setCustomerId("");
      setCustomers([]);
    }
  }, [open]);

  useEffect(() => {
    if (role === "customer" && orgId) {
      api.listCustomers().then((c: { id: number; name: string; organization_id?: number }[]) => {
        setCustomers(c);
      }).catch(() => setCustomers([]));
    } else {
      setCustomerId("");
    }
  }, [role, orgId]);

  const needsOrg = role === "owner" || role === "member" || role === "customer";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.createUser({
        username,
        password,
        role,
        organization_id: needsOrg ? (orgId as number) : null,
        customer_id: role === "customer" && customerId ? (customerId as number) : null,
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
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="courier">Koerier</SelectItem>
                <SelectItem value="owner">Eigenaar (organisatie)</SelectItem>
                <SelectItem value="member">Medewerker (organisatie)</SelectItem>
                <SelectItem value="customer">Klant (organisatie)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {needsOrg && (
            <div className="space-y-2">
              <Label>Organisatie</Label>
              <Select
                value={orgId ? String(orgId) : ""}
                onValueChange={(v) => setOrgId(v ? Number(v) : "")}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Selecteer organisatie..." />
                </SelectTrigger>
                <SelectContent>
                  {organizations.map((org) => (
                    <SelectItem key={org.id} value={String(org.id)}>
                      {org.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          {role === "customer" && orgId && (
            <div className="space-y-2">
              <Label>Koppel aan klant</Label>
              <Select
                value={customerId ? String(customerId) : ""}
                onValueChange={(v) => setCustomerId(v ? Number(v) : "")}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Selecteer klant..." />
                </SelectTrigger>
                <SelectContent>
                  {customers.map((c) => (
                    <SelectItem key={c.id} value={String(c.id)}>
                      {c.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                De gebruiker kan alleen orders plaatsen voor deze klant
              </p>
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

function NewCustomerDialog({
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
  const { user: me } = useAuth();
  const [name, setName] = useState("");
  const [orgId, setOrgId] = useState<number | "">("");
  const [showPrices, setShowPrices] = useState(true);

  useEffect(() => {
    if (open) {
      setName("");
      setOrgId(me?.organization_id || "");
      setShowPrices(true);
    }
  }, [open, me]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.createCustomer({
        name,
        organization_id: orgId ? (orgId as number) : undefined,
        show_prices: showPrices,
      });
      toast.success(`Klant '${name}' aangemaakt`);
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
          <DialogTitle>Nieuwe klant</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label>Naam</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              minLength={1}
              required
              autoFocus
            />
          </div>
          {me?.is_platform_admin && (
            <div className="space-y-2">
              <Label>Organisatie</Label>
              <Select
                value={orgId ? String(orgId) : ""}
                onValueChange={(v) => setOrgId(v ? Number(v) : "")}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Selecteer organisatie..." />
                </SelectTrigger>
                <SelectContent>
                  {organizations.map((org) => (
                    <SelectItem key={org.id} value={String(org.id)}>
                      {org.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <div className="flex items-center gap-3">
            <Switch
              id="show-prices-new"
              checked={showPrices}
              onCheckedChange={setShowPrices}
            />
            <Label htmlFor="show-prices-new">Prijzen zichtbaar voor klant</Label>
          </div>
          <Button type="submit" className="w-full">
            Aanmaken
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EditCustomerDialog({
  customer,
  onClose,
  onUpdated,
}: {
  customer: Customer | null;
  onClose: () => void;
  onUpdated: () => void;
}) {
  const [name, setName] = useState("");
  const [showPrices, setShowPrices] = useState(true);

  useEffect(() => {
    if (customer) {
      setName(customer.name);
      setShowPrices(customer.show_prices);
    }
  }, [customer]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!customer) return;
    try {
      await api.updateCustomer(customer.id, {
        name: name.trim(),
        show_prices: showPrices,
      });
      toast.success("Klant bijgewerkt");
      onClose();
      onUpdated();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    }
  }

  return (
    <Dialog open={!!customer} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Klant bewerken</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label>Naam</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              minLength={1}
              required
              autoFocus
            />
          </div>
          <div className="flex items-center gap-3">
            <Switch
              id="show-prices-edit"
              checked={showPrices}
              onCheckedChange={setShowPrices}
            />
            <Label htmlFor="show-prices-edit">Prijzen zichtbaar voor klant</Label>
          </div>
          <Button type="submit" className="w-full">
            Opslaan
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function NewSupplierDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");

  useEffect(() => {
    if (open) setName("");
  }, [open]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.createSupplier({ name: name.trim() });
      toast.success(`Leverancier '${name.trim()}' aangemaakt`);
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
          <DialogTitle>Nieuwe leverancier</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label>Naam</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Bijv. Domaine Leflaive"
              minLength={1}
              required
              autoFocus
            />
          </div>
          <Button type="submit" className="w-full">
            Aanmaken
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
