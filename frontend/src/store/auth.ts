import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface User {
  id: string;
  email: string;
  name: string | null;
  base_currency: string;
  subscription: "free" | "pro" | "business";
  sub_expires_at: string | null;
  email_verified: boolean;
  created_at: string;
}

interface AuthState {
  user: User | null;
  accessToken: string | null;
  refreshToken: string | null;
  setSession: (user: User, accessToken: string, refreshToken: string) => void;
  setTokens: (accessToken: string, refreshToken: string) => void;
  setUser: (user: User) => void;
  clear: () => void;
}

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      accessToken: null,
      refreshToken: null,
      setSession: (user, accessToken, refreshToken) =>
        set({ user, accessToken, refreshToken }),
      setTokens: (accessToken, refreshToken) => set({ accessToken, refreshToken }),
      setUser: (user) => set({ user }),
      clear: () => set({ user: null, accessToken: null, refreshToken: null }),
    }),
    {
      name: "kapital-auth",
      // Never persist the refresh token to localStorage — it lives only in an
      // httpOnly cookie, out of reach of XSS. The short-lived access token is
      // kept so route guards survive a reload.
      partialize: (s) => ({ user: s.user, accessToken: s.accessToken }),
    },
  ),
);
