import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, setUnauthorizedHandler } from "./api";

interface Auth {
  signedIn: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [signedIn, setSignedIn] = useState(api.hasToken());

  useEffect(() => setUnauthorizedHandler(() => setSignedIn(false)), []);

  const login = useCallback(async (email: string, password: string) => {
    await api.login(email, password);
    setSignedIn(true);
  }, []);
  const logout = useCallback(() => {
    api.logout();
    setSignedIn(false);
  }, []);

  const value = useMemo(() => ({ signedIn, login, logout }), [signedIn, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
