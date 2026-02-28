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
import { Trash2 } from "lucide-react";

interface User {
  id: number;
  username: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

const ROLE_LABELS: Record<string, string> = {
  admin: "Admin",
  merchant: "Wijnhandelaar",
  courier: "Koerier",
};

export function AccountsPage() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [showNew, setShowNew] = useState(false);

  const load = useCallback(async () => {
    try {
      setUsers(await api.listUsers());
    } catch {
      toast.error("Kan gebruikers niet laden");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDelete(u: User) {
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

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Accounts</h2>
        <Button size="sm" onClick={() => setShowNew(true)}>
          + Nieuw
        </Button>
      </div>

      <div className="space-y-3">
        {users.map((u) => (
          <Card key={u.id} className="p-4">
            <div className="flex justify-between items-center">
              <div>
                <p className="font-semibold">{u.username}</p>
                <div className="flex gap-2 mt-1">
                  <Badge variant={u.role === "admin" ? "default" : "secondary"}>
                    {ROLE_LABELS[u.role] || u.role}
                  </Badge>
                </div>
              </div>
              {u.id !== me?.id && (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => handleDelete(u)}
                >
                  <Trash2 className="h-4 w-4 text-destructive" />
                </Button>
              )}
            </div>
          </Card>
        ))}
      </div>

      <NewUserDialog
        open={showNew}
        onClose={() => setShowNew(false)}
        onCreated={load}
      />
    </>
  );
}

function NewUserDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("courier");

  useEffect(() => {
    if (open) {
      setUsername("");
      setPassword("");
      setRole("courier");
    }
  }, [open]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.createUser({ username, password, role });
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
              minLength={6}
              required
            />
          </div>
          <div className="space-y-2">
            <Label>Rol</Label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              <option value="courier">Koerier</option>
              <option value="merchant">Wijnhandelaar</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <Button type="submit" className="w-full">
            Aanmaken
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
