import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  PlusCircle,
  Sparkles,
  GitCompareArrows,
  Settings,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

/**
 * Mobile-first bottom navigation. Sticky on small screens; on >= md it stays
 * but is comfortably reachable. Hidden on the auth screen (rendered only inside
 * the authenticated shell).
 */
export function BottomNav() {
  const t = useT();
  const ITEMS = [
    { to: "/", label: t("Капитал", "Wealth"), icon: LayoutDashboard, end: true },
    { to: "/input", label: t("Добавить", "Add"), icon: PlusCircle, end: false },
    { to: "/advisor", label: t("Советник", "Advisor"), icon: Sparkles, end: false },
    { to: "/scenarios", label: t("Сценарии", "Scenarios"), icon: GitCompareArrows, end: false },
    { to: "/settings", label: t("Профиль", "Profile"), icon: Settings, end: false },
  ];
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
      <div className="mx-auto flex max-w-4xl items-stretch justify-around px-2 pb-[env(safe-area-inset-bottom)]">
        {ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                "flex flex-1 flex-col items-center gap-0.5 py-2 text-xs transition-colors",
                isActive
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )
            }
          >
            {({ isActive }) => (
              <>
                <Icon
                  className={cn("h-5 w-5", isActive && "text-indigo-400")}
                  strokeWidth={isActive ? 2.4 : 1.8}
                />
                <span>{label}</span>
              </>
            )}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
