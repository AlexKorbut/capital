// Theme registry — every saved design from the mockup gallery, with neutral
// English names. Each theme maps a small palette onto the shadcn token system
// (--background / --foreground / --card / --primary / … ) plus a font pair.
// Switching is purely client-side: applyTheme() writes CSS variables on <html>.

export type ThemeMode = "dark" | "light";

export interface ThemeTokens {
  bg: string;
  fg: string;
  card: string;
  muted: string;
  mutedFg: string;
  primary: string;
  primaryFg: string;
  border: string;
  radius: string; // CSS length, e.g. "0.75rem"
}

export interface Theme {
  id: string;
  name: string; // neutral English label
  group: string;
  mode: ThemeMode;
  tokens: ThemeTokens;
  fontSans: string;
  fontDisplay: string;
  fonts: string[]; // Google Fonts "family=" query fragments
  swatches: [string, string, string, string];
}

export const THEME_STORAGE_KEY = "kapital-theme";
export const DEFAULT_THEME_ID = "marketplace";

// ---- Google Fonts family fragments (reused across themes) ------------------
const F = {
  inter: "Inter:wght@400;500;600;700;800",
  fraunces: "Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700",
  newsreader: "Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600;6..72,700",
  plexMono: "IBM+Plex+Mono:wght@400;500;600",
  spectral: "Spectral:wght@400;500;600;700",
  montserrat: "Montserrat:wght@400;500;600;700;800;900",
};

const SANS = "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
const SERIF = "Georgia, 'Times New Roman', serif";

const G_MINIMAL = "Минимал и премиум";
const G_BRAND = "Бренд-стили";

export const THEMES: Theme[] = [
  // ---------------- Минимал и премиум ----------------
  {
    // "Светлая премиум-тема"
    id: "linen", name: "Linen", group: G_MINIMAL, mode: "light",
    tokens: { bg: "#f4f1ea", fg: "#1c1f1b", card: "#ffffff", muted: "#faf8f3", mutedFg: "#6f7268", primary: "#1f5d43", primaryFg: "#f4f1ea", border: "#e4ded2", radius: "1.125rem" },
    fontSans: `Inter, ${SANS}`, fontDisplay: `Fraunces, ${SERIF}`, fonts: [F.inter, F.fraunces],
    swatches: ["#f4f1ea", "#1f5d43", "#b06a3f", "#ffffff"],
  },
  {
    // "Тёплый матовый минимал" (вариант C) — тёплый off-black, янтарь, Newsreader
    id: "amber", name: "Amber", group: G_MINIMAL, mode: "dark",
    tokens: { bg: "#13110f", fg: "#f0ebe4", card: "#201c18", muted: "#1a1714", mutedFg: "#9a9085", primary: "#e0a16a", primaryFg: "#1c1407", border: "#2b2620", radius: "1.125rem" },
    fontSans: `Inter, ${SANS}`, fontDisplay: `Newsreader, ${SERIF}`, fonts: [F.inter, F.newsreader],
    swatches: ["#13110f", "#e0a16a", "#a9b89a", "#201c18"],
  },
  {
    // "Apple-стиль"
    id: "frost", name: "Frost", group: G_MINIMAL, mode: "light",
    tokens: { bg: "#fbfbfd", fg: "#1d1d1f", card: "#ffffff", muted: "#f5f5f7", mutedFg: "#86868b", primary: "#0071e3", primaryFg: "#ffffff", border: "#e3e3e8", radius: "1.375rem" },
    fontSans: `-apple-system, 'SF Pro Display', Inter, ${SANS}`, fontDisplay: `-apple-system, 'SF Pro Display', Inter, ${SANS}`, fonts: [F.inter],
    swatches: ["#fbfbfd", "#0071e3", "#1d1d1f", "#f5f5f7"],
  },
  {
    // "Архитектурный камень"
    id: "limestone", name: "Limestone", group: G_MINIMAL, mode: "light",
    tokens: { bg: "#e7e2d6", fg: "#24221c", card: "#f2eee4", muted: "#ece7da", mutedFg: "#7a7568", primary: "#3c5060", primaryFg: "#f2eee4", border: "#d2cbba", radius: "0.375rem" },
    fontSans: `Inter, ${SANS}`, fontDisplay: `Spectral, ${SERIF}`, fonts: [F.inter, F.spectral],
    swatches: ["#e7e2d6", "#3c5060", "#b06a44", "#f2eee4"],
  },

  // ---------------- Бренд-стили ----------------
  {
    // в стиле Amazon — основная по умолчанию
    id: "marketplace", name: "Marketplace", group: G_BRAND, mode: "light",
    tokens: { bg: "#ffffff", fg: "#0f1111", card: "#f7f8f8", muted: "#eaeded", mutedFg: "#565959", primary: "#ff9900", primaryFg: "#0f1111", border: "#d5d9d9", radius: "0.5rem" },
    fontSans: `Inter, ${SANS}`, fontDisplay: `Inter, ${SANS}`, fonts: [F.inter],
    swatches: ["#131921", "#ff9900", "#ffd814", "#f7f8f8"],
  },
  {
    // в стиле Binance
    id: "terminal", name: "Terminal", group: G_BRAND, mode: "dark",
    tokens: { bg: "#0b0e11", fg: "#eaecef", card: "#1e2026", muted: "#181a20", mutedFg: "#848e9c", primary: "#fcd535", primaryFg: "#0b0e11", border: "#2b3139", radius: "0.5rem" },
    fontSans: `Inter, ${SANS}`, fontDisplay: `Inter, ${SANS}`, fonts: [F.inter, F.plexMono],
    swatches: ["#0b0e11", "#fcd535", "#0ecb81", "#f6465d"],
  },
  {
    // в стиле Stripe
    id: "prism", name: "Prism", group: G_BRAND, mode: "light",
    tokens: { bg: "#ffffff", fg: "#0a2540", card: "#f6f9fc", muted: "#eef3f8", mutedFg: "#425466", primary: "#635bff", primaryFg: "#ffffff", border: "#e6ebf1", radius: "0.875rem" },
    fontSans: `Inter, ${SANS}`, fontDisplay: `Inter, ${SANS}`, fonts: [F.inter],
    swatches: ["#635bff", "#0a2540", "#11c8a6", "#f6f9fc"],
  },
  {
    // в стиле Tesla
    id: "velocity", name: "Velocity", group: G_BRAND, mode: "light",
    tokens: { bg: "#ffffff", fg: "#171a20", card: "#ffffff", muted: "#f4f4f4", mutedFg: "#5c5e62", primary: "#e82127", primaryFg: "#ffffff", border: "#e3e3e3", radius: "0.375rem" },
    fontSans: `Montserrat, ${SANS}`, fontDisplay: `Montserrat, ${SANS}`, fonts: [F.montserrat],
    swatches: ["#ffffff", "#e82127", "#171a20", "#f4f4f4"],
  },
];

export const THEME_GROUPS: string[] = [G_MINIMAL, G_BRAND];

// ---- helpers ---------------------------------------------------------------

function hexToHsl(hex: string): string {
  let h = hex.replace("#", "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  const r = parseInt(h.slice(0, 2), 16) / 255;
  const g = parseInt(h.slice(2, 4), 16) / 255;
  const b = parseInt(h.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let hue = 0;
  let sat = 0;
  const d = max - min;
  if (d !== 0) {
    sat = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case r:
        hue = (g - b) / d + (g < b ? 6 : 0);
        break;
      case g:
        hue = (b - r) / d + 2;
        break;
      default:
        hue = (r - g) / d + 4;
    }
    hue /= 6;
  }
  return `${Math.round(hue * 360)} ${Math.round(sat * 100)}% ${Math.round(l * 100)}%`;
}

function buildFontHref(families: string[]): string | null {
  if (!families.length) return null;
  return (
    "https://fonts.googleapis.com/css2?" +
    families.map((f) => "family=" + f).join("&") +
    "&display=swap"
  );
}

export function getTheme(id: string): Theme {
  return (
    THEMES.find((t) => t.id === id) ??
    THEMES.find((t) => t.id === DEFAULT_THEME_ID)!
  );
}

export function getStoredThemeId(): string {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY) || DEFAULT_THEME_ID;
  } catch {
    return DEFAULT_THEME_ID;
  }
}

export function applyTheme(t: Theme): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  const set = (k: string, hex: string) => root.style.setProperty(k, hexToHsl(hex));

  set("--background", t.tokens.bg);
  set("--foreground", t.tokens.fg);
  set("--card", t.tokens.card);
  set("--card-foreground", t.tokens.fg);
  set("--primary", t.tokens.primary);
  set("--primary-foreground", t.tokens.primaryFg);
  set("--muted", t.tokens.muted);
  set("--muted-foreground", t.tokens.mutedFg);
  set("--border", t.tokens.border);
  set("--input", t.tokens.border);
  set("--ring", t.tokens.primary);
  root.style.setProperty("--radius", t.tokens.radius);
  root.style.setProperty("--font-sans", t.fontSans);
  root.style.setProperty("--font-display", t.fontDisplay);

  root.style.colorScheme = t.mode;
  root.setAttribute("data-theme", t.id);
  root.classList.toggle("dark", t.mode === "dark");

  const href = buildFontHref(t.fonts);
  if (href) {
    let link = document.getElementById("theme-font") as HTMLLinkElement | null;
    if (!link) {
      link = document.createElement("link");
      link.id = "theme-font";
      link.rel = "stylesheet";
      document.head.appendChild(link);
    }
    if (link.href !== href) link.href = href;
  }

  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", t.tokens.bg);
}

export function initTheme(): void {
  applyTheme(getTheme(getStoredThemeId()));
}
