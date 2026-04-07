import { useState } from "react";
import { Toaster, toast } from "sonner";
import { AuthProvider, useAuth } from "@/lib/auth";
import { LoginPage } from "@/components/login";
import { ReceivePage } from "@/components/receive";
import { SKUsPage } from "@/components/skus";
import { OrdersPage } from "@/components/orders";
import { AccountsPage } from "@/components/accounts";
import { InventoryPage } from "@/components/inventory";
import { InboundPage } from "@/components/inbound";
import { WeeklySummaryPage } from "@/components/weekly-summary";
import { LogOut } from "lucide-react";

export { toast };

type Page = "orders" | "receive" | "inbound" | "skus" | "inventory" | "accounts" | "weekly";

function Main() {
  const { user, loading, logout } = useAuth();
  const defaultPage: Page = user?.role === "courier" ? "receive" : "orders";
  const [page, setPage] = useState<Page>(defaultPage);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-muted-foreground">Laden...</p>
      </div>
    );
  }

  if (!user) return <LoginPage />;

  const isAdmin = user.is_platform_admin;

  const tabs: { id: Page; label: string; show: boolean }[] = [
    {
      id: "orders",
      label: "Orders",
      show: true, // Everyone sees orders
    },
    {
      id: "receive",
      label: "Scan & Boek",
      show: isAdmin || user.role === "courier",
    },
    {
      id: "inbound",
      label: "Inbound",
      show: isAdmin || user.role === "courier" || user.role === "owner" || user.role === "member",
    },
    {
      id: "skus",
      label: "Producten",
      show: isAdmin || user.role === "owner" || user.role === "member",
    },
    {
      id: "inventory",
      label: "Voorraad",
      show: isAdmin || user.role === "owner" || user.role === "member",
    },
    {
      id: "weekly",
      label: "Weekoverzicht",
      show: isAdmin || user.role === "owner" || user.role === "member",
    },
    {
      id: "accounts",
      label: isAdmin ? "Accounts" : "Klanten",
      show: isAdmin || user.role === "owner" || user.role === "member",
    },
  ];

  const visibleTabs = tabs.filter((t) => t.show);

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
        {page === "orders" && <OrdersPage />}
        {page === "receive" && <ReceivePage />}
        {page === "inbound" && <InboundPage />}
        {page === "skus" && <SKUsPage />}
        {page === "inventory" && <InventoryPage />}
        {page === "weekly" && <WeeklySummaryPage />}
        {page === "accounts" && <AccountsPage />}
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
