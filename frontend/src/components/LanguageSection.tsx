import { Languages } from "lucide-react";
import { useLang, useT, type Lang } from "@/lib/i18n";
import { setLanguagePref } from "@/services/account";

const OPTIONS: { code: Lang; label: string }[] = [
  { code: "ru", label: "Русский" },
  { code: "en", label: "English" },
];

export function LanguageSection() {
  const t = useT();
  const { lang, setLang } = useLang();

  const choose = (code: Lang) => {
    setLang(code);
    // Sync to the account so advisor output and emails follow the language.
    setLanguagePref(code).catch(() => {
      /* best-effort; UI language already applied locally */
    });
  };

  return (
    <section className="mt-6 rounded-lg border border-border bg-card p-6">
      <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
        <Languages className="h-4 w-4" />
        {t("Язык", "Language")}
      </h2>
      <div className="mt-3 flex gap-2">
        {OPTIONS.map((o) => (
          <button
            key={o.code}
            type="button"
            onClick={() => choose(o.code)}
            className={`rounded-md border px-4 py-1.5 text-sm transition ${
              o.code === lang
                ? "border-primary bg-primary/10 text-primary"
                : "border-border hover:border-muted-foreground/50"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </section>
  );
}
