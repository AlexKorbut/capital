import { Link, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  Mic,
  Image as ImageIcon,
  FileSpreadsheet,
  Globe2,
  Newspaper,
  Sparkles,
  ShieldCheck,
  LineChart,
  Lock,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

type T = (ru: string, en: string) => string;

const buildInputs = (t: T) => [
  { icon: Sparkles, label: t("Текст", "Text"), hint: t('«10к евро налик, 2 битка, депозит 1000 лари»', '"€10k cash, 2 BTC, a 1000 GEL deposit"') },
  { icon: Mic, label: t("Голос", "Voice"), hint: t("Надиктуйте активы — Whisper расшифрует", "Dictate assets — Whisper transcribes") },
  { icon: ImageIcon, label: t("Фото", "Photo"), hint: t("Скрин брокера или кошелька", "A broker or wallet screenshot") },
  { icon: FileSpreadsheet, label: "Excel", hint: t("Выгрузка из банка/биржи", "Export from a bank/exchange") },
];

const buildFeatures = (t: T) => [
  {
    icon: Globe2,
    title: t("Мультивалюта по курсам ЦБ", "Multi-currency at central-bank rates"),
    body: t(
      "Фиат и крипта сводятся в одной валюте: ЕЦБ + нацбанки для BYN/GEL, CoinGecko для монет. Точность до копейки на Decimal.",
      "Fiat and crypto in one currency: ECB + national banks for BYN/GEL, CoinGecko for coins. Penny-precise with Decimal.",
    ),
  },
  {
    icon: LineChart,
    title: t("Динамика капитала", "Net-worth over time"),
    body: t(
      "Net worth, разбивка по классам и странам, история стоимости — снимок за снимком.",
      "Net worth, breakdown by class and country, value history — snapshot by snapshot.",
    ),
  },
  {
    icon: Newspaper,
    title: t("Гео и новости под ваш портфель", "Geo & news tailored to your portfolio"),
    body: t(
      "Параллельные агенты анализируют возможности по странам и фильтруют новости именно по вашим активам.",
      "Parallel agents analyze opportunities by country and filter news to your actual holdings.",
    ),
  },
  {
    icon: Sparkles,
    title: t("AI-советник, а не брокер", "An AI advisor, not a broker"),
    body: t(
      "Персональные инсайты и сценарии «что если» — без «купи/продай». Аналитика ваших данных, с дисклеймером.",
      "Personal insights and what-if scenarios — no buy/sell calls. Analysis of your data, with a disclaimer.",
    ),
  },
  {
    icon: Lock,
    title: t("Приватность в ядре", "Privacy at the core"),
    body: t(
      "Чувствительные поля шифруются at-rest (Fernet/AES). Экспорт и удаление аккаунта по GDPR — в один клик.",
      "Sensitive fields are encrypted at rest (Fernet/AES). GDPR export and account deletion in one click.",
    ),
  },
  {
    icon: ShieldCheck,
    title: t("Готов к оплате", "Ready to sell"),
    body: t(
      "Тарифы Free / Pro / Business. Карта через Stripe, крипта через CoinGate (USDT/BTC).",
      "Free / Pro / Business plans. Card via Stripe, crypto via CoinGate (USDT/BTC).",
    ),
  },
];

export function LandingPage() {
  const t = useT();
  const INPUTS = buildInputs(t);
  const FEATURES = buildFeatures(t);
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Nav */}
      <header className="mx-auto flex max-w-5xl items-center justify-between px-4 py-5">
        <span className="text-lg font-bold tracking-tight">КАПИТАЛЬ</span>
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={() => navigate("/auth")}>
            {t("Войти", "Sign in")}
          </Button>
          <Button onClick={() => navigate("/auth?mode=register")}>
            {t("Начать бесплатно", "Start free")}
          </Button>
        </div>
      </header>

      {/* Hero */}
      <section className="mx-auto max-w-5xl px-4 pb-16 pt-10 text-center sm:pt-20">
        <span className="inline-flex items-center gap-2 rounded-full border border-indigo-500/40 bg-indigo-500/10 px-3 py-1 text-xs text-indigo-300">
          <Sparkles className="h-3.5 w-3.5" />
          {t(
            "Приватный трекер капитала с AI-советником",
            "A private wealth tracker with an AI advisor",
          )}
        </span>
        <h1 className="mx-auto mt-6 max-w-3xl text-4xl font-extrabold leading-tight tracking-tight sm:text-6xl">
          {t("Весь капитал — в одном месте.", "All your wealth — in one place.")}
          <span className="block bg-gradient-to-r from-indigo-400 to-emerald-400 bg-clip-text text-transparent">
            {t("Считает, анализирует, подсказывает.", "It counts, analyzes, advises.")}
          </span>
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-base text-muted-foreground sm:text-lg">
          {t(
            "Добавьте активы текстом, голосом, фото или из Excel — КАПИТАЛЬ распознаёт их, сводит в любую валюту по курсам ЦБ и даёт персональные инсайты. Без таблиц, без ручного ввода курсов, без передачи данных третьим лицам.",
            "Add assets by text, voice, photo or Excel — KAPITAL recognizes them, converts to any currency at central-bank rates and gives personal insights. No spreadsheets, no manual rates, no sharing data with third parties.",
          )}
        </p>
        <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Button
            className="w-full sm:w-auto"
            onClick={() => navigate("/auth?mode=register")}
          >
            {t("Начать бесплатно", "Start free")}
            <ArrowRight className="ml-2 h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            className="w-full sm:w-auto"
            onClick={() => navigate("/auth")}
          >
            {t("У меня есть аккаунт", "I have an account")}
          </Button>
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          {t("Free навсегда · карта не нужна для старта", "Free forever · no card to start")}
        </p>
      </section>

      {/* Input modes */}
      <section className="mx-auto max-w-5xl px-4 pb-16">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {INPUTS.map(({ icon: Icon, label, hint }) => (
            <div
              key={label}
              className="rounded-xl border border-border bg-card p-5"
            >
              <Icon className="h-5 w-5 text-indigo-400" />
              <p className="mt-3 font-semibold">{label}</p>
              <p className="mt-1 text-sm text-muted-foreground">{hint}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Features */}
      <section className="mx-auto max-w-5xl px-4 pb-16">
        <h2 className="text-center text-2xl font-bold sm:text-3xl">
          {t("11 агентов работают на ваш капитал", "11 agents working for your wealth")}
        </h2>
        <div className="mt-10 grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map(({ icon: Icon, title, body }) => (
            <div
              key={title}
              className="rounded-xl border border-border bg-card p-6"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-500/10">
                <Icon className="h-5 w-5 text-indigo-400" />
              </div>
              <h3 className="mt-4 font-semibold">{title}</h3>
              <p className="mt-2 text-sm text-muted-foreground">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-5xl px-4 pb-20">
        <div className="rounded-2xl border border-indigo-500/40 bg-gradient-to-br from-indigo-500/10 to-emerald-500/5 p-10 text-center">
          <h2 className="text-2xl font-bold sm:text-3xl">
            {t("Возьмите капитал под контроль сегодня", "Take control of your wealth today")}
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-sm text-muted-foreground">
            {t(
              "Регистрация занимает минуту. Free-тариф — навсегда, без карты.",
              "Sign-up takes a minute. Free plan — forever, no card.",
            )}
          </p>
          <Button
            className="mt-6"
            onClick={() => navigate("/auth?mode=register")}
          >
            {t("Создать аккаунт", "Create account")}
            <ArrowRight className="ml-2 h-4 w-4" />
          </Button>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-5xl flex-col items-center justify-between gap-3 px-4 py-6 text-xs text-muted-foreground sm:flex-row">
          <span>© {new Date().getFullYear()} КАПИТАЛЬ</span>
          <div className="flex gap-4">
            <Link to="/legal/tos" className="hover:text-foreground">
              {t("Условия", "Terms")}
            </Link>
            <Link to="/legal/privacy" className="hover:text-foreground">
              {t("Конфиденциальность", "Privacy")}
            </Link>
            <Link to="/legal/disclaimer" className="hover:text-foreground">
              {t("Дисклеймер", "Disclaimer")}
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
