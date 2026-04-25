import { Toaster, toast } from "sonner";
import { AuthProvider, useAuth } from "@/lib/auth";
import { LoginPage } from "@/components/login";
import { ReceivePage } from "@/components/receive";
import { SKUsPage } from "@/components/skus";
import { OrdersPage } from "@/components/orders";
import { AccountsPage } from "@/components/accounts";
import { CustomersPage } from "@/components/customers";
import { InventoryPage } from "@/components/inventory";
import { InboundPage } from "@/components/inbound";
import { WeeklySummaryPage } from "@/components/weekly-summary";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { LogOut } from "lucide-react";

export { toast };

type Page = "orders" | "receive" | "inbound" | "skus" | "inventory" | "customers" | "accounts" | "weekly";

function Main() {
  const { user, loading, logout } = useAuth();
  const defaultPage: Page = user?.role === "courier" ? "receive" : "orders";

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
      show: true,
    },
    {
      id: "receive",
      label: "Scan & Boek",
      show: isAdmin || user.role === "courier",
    },
    {
      id: "inbound",
      label: "Inbound",
      show: isAdmin || user.role === "courier",
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
      id: "customers",
      label: "Klanten",
      show: isAdmin || user.role === "owner" || user.role === "member",
    },
    {
      id: "accounts",
      label: isAdmin ? "Beheer" : "Leveranciers",
      show: isAdmin || user.role === "owner",
    },
  ];

  const visibleTabs = tabs.filter((t) => t.show);

  return (
    <Tabs defaultValue={defaultPage} className="min-h-screen flex flex-col">
      <header className="sticky top-0 z-50 bg-background border-b border-border px-4 pt-3 pb-0">
        <div className="flex justify-between items-center mb-2">
          {user.role === "customer" ? (
            <img src="/jurjen-logo.png" alt="Jurjen Wijn" className="h-8 object-contain" />
          ) : (
            <h1 className="text-lg font-bold">{user.custom_label || "Magazijn"}</h1>
          )}
          <button
            onClick={logout}
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <span>{user.username}</span>
            <LogOut className="h-4 w-4" />
          </button>
        </div>
        <TabsList>
          {visibleTabs.map((t) => (
            <TabsTrigger key={t.id} value={t.id}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </header>

      <main className="flex-1 p-4 pb-20">
        {visibleTabs.map((t) => (
          <TabsContent key={t.id} value={t.id}>
            {t.id === "orders" && <OrdersPage />}
            {t.id === "receive" && <ReceivePage />}
            {t.id === "inbound" && <InboundPage />}
            {t.id === "skus" && <SKUsPage />}
            {t.id === "inventory" && <InventoryPage />}
            {t.id === "weekly" && <WeeklySummaryPage />}
            {t.id === "customers" && <CustomersPage />}
            {t.id === "accounts" && <AccountsPage />}
          </TabsContent>
        ))}
      </main>
    </Tabs>
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
