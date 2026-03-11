import React, { createContext, useContext, useState, useEffect } from "react";
import { api, setToken, setRefreshToken, clearToken } from "./api";

type Role = "admin" | "merchant" | "courier";

interface AuthUser {
  id: number;
  username: string;
  role: Role;
}

interface AuthCtx {
  user: AuthUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthCtx>(null!);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      setLoading(false);
      return;
    }
    api
      .me()
      .then((u) => setUser(u))
      .catch(() => clearToken())
      .finally(() => setLoading(false));
  }, []);

  async function login(username: string, password: string) {
    const resp = await api.login(username, password);
    setToken(resp.access_token);
    setRefreshToken(resp.refresh_token);
    setUser({ id: 0, username: resp.username, role: resp.role });
    // Refresh full user data
    const me = await api.me();
    setUser(me);
  }

  function logout() {
    clearToken();
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
