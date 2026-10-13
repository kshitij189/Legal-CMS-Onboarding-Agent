"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, getToken, setToken } from "@/lib/api";
import type { Me } from "@/lib/types";

interface Session {
  me: Me | null;
  loading: boolean;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  can: (permission: string) => boolean;
}

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const loadMe = useCallback(async () => {
    if (!getToken()) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      setMe(await api<Me>("/auth/me"));
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadMe();
  }, [loadMe]);

  const signIn = useCallback(
    async (username: string, password: string) => {
      const token = await api<{ access_token: string }>("/auth/token", {
        method: "POST",
        body: { username, password },
      });
      setToken(token.access_token);
      await loadMe();
    },
    [loadMe],
  );

  const signOut = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      setToken(null);
      setMe(null);
    }
  }, []);

  const can = useCallback((permission: string) => !!me?.permissions.includes(permission), [me]);

  return (
    <SessionContext.Provider value={{ me, loading, signIn, signOut, can }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider");
  return session;
}
