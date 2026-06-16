import { create } from "zustand";
import {
  THEME_STORAGE_KEY,
  applyTheme,
  getStoredThemeId,
  getTheme,
} from "@/lib/theme";

interface ThemeState {
  themeId: string;
  setTheme: (id: string) => void;
}

export const useTheme = create<ThemeState>((set) => ({
  themeId: getStoredThemeId(),
  setTheme: (id) => {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, id);
    } catch {
      /* ignore storage errors (private mode) */
    }
    applyTheme(getTheme(id));
    set({ themeId: id });
  },
}));
