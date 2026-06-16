import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Lang = "ru" | "en";

interface LangState {
  lang: Lang;
  setLang: (lang: Lang) => void;
}

function applyHtmlLang(lang: Lang) {
  if (typeof document !== "undefined") {
    document.documentElement.lang = lang;
  }
}

export const useLang = create<LangState>()(
  persist(
    (set) => ({
      lang: "ru",
      setLang: (lang) => {
        applyHtmlLang(lang);
        set({ lang });
      },
    }),
    {
      name: "kapital-lang",
      onRehydrateStorage: () => (state) => {
        if (state) applyHtmlLang(state.lang);
      },
    },
  ),
);

/**
 * Translation helper. Pass the Russian (default) and English strings inline:
 *   const t = useT();
 *   <h1>{t("Настройки", "Settings")}</h1>
 * Co-locating both languages keeps a fully-built RU app easy to retrofit.
 */
export function useT(): (ru: string, en: string) => string {
  const lang = useLang((s) => s.lang);
  return (ru: string, en: string) => (lang === "en" ? en : ru);
}

/** Non-hook variant for code outside React render (selectors, maps). */
export function tr(ru: string, en: string): string {
  return useLang.getState().lang === "en" ? en : ru;
}
