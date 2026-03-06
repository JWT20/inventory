import { useState } from "react";
import { Toaster, toast } from "sonner";
import { AuthProvider, useAuth } from "@/lib/auth";
import { LoginPage } from "@/components/login";
import { ReceivePage } from "@/components/receive";
import { SKUsPage } from "@/components/skus";
import { OrdersPage } from "@/components/orders";
import { AccountsPage } from "@/components/accounts";
import { LogOut } from "lucide-react";

export { toast };

type Page = "receive" | "orders" | "skus" | "accounts";

function Main() {
  const { user, loading, logout } = useAuth();
  const [page, setPage] = useState<Page>("receive");

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-muted-foreground">Laden...</p>
      </div>
    );
  }

  if (!user) return <LoginPage />;

  const tabs: { id: Page; label: string; managerOnly?: boolean; adminOnly?: boolean }[] = [
    { id: "receive", label: "Scan & Sorteer" },
    { id: "orders", label: "Orders", managerOnly: true },
    { id: "skus", label: "Producten" },
    { id: "accounts", label: "Accounts", adminOnly: true },
  ];

  const visibleTabs = tabs.filter((t) => {
    if (t.adminOnly) return user.role === "admin";
    if (t.managerOnly) return user.role === "admin" || user.role === "merchant";
    return true;
  });

  return (
    <div className="min-h-screen flex flex-col">
      <header className="sticky top-0 z-50 bg-background border-b border-border px-4 pt-3 pb-0">
        <div className="flex justify-between items-center mb-2">
          <h1 className="text-lg font-bold">Magazijn</h1>
          <button
            onClick={logout}
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <span>{user.username}</span>
            <LogOut className="h-4 w-4" />
          </button>
        </div>
        <nav className="flex gap-1">
          {visibleTabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setPage(t.id)}
              className={`flex-1 py-2.5 text-sm font-semibold border-b-2 transition-colors ${
                page === t.id
                  ? "text-primary border-primary"
                  : "text-muted-foreground border-transparent hover:text-foreground"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      <main className="flex-1 p-4 pb-20">
        {page === "receive" && <ReceivePage />}
        {page === "orders" && <OrdersPage />}
        {page === "skus" && <SKUsPage />}
        {page === "accounts" && user.role === "admin" && <AccountsPage />}
      </main>
    </div>
  );
}

export function App() {
  return (
    <AuthProvider>
      <Main />
      <Toaster position="bottom-center" />
    </AuthProvider>
  );
}
