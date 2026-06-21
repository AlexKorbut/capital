import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Wallet as WalletIcon, X } from "lucide-react";
import {
  addWallet,
  deleteWallet,
  listWallets,
  syncWallets,
  type Chain,
} from "@/services/wallets";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/lib/i18n";
import { formatUsd as usd } from "@/lib/utils";

const CHAINS: Chain[] = ["BTC", "ETH", "TON"];

export function WalletsSection() {
  const t = useT();
  const qc = useQueryClient();
  const wallets = useQuery({ queryKey: ["wallets"], queryFn: listWallets });
  const [chain, setChain] = useState<Chain>("BTC");
  const [address, setAddress] = useState("");
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["wallets"] });
    qc.invalidateQueries({ queryKey: ["portfolio"] });
  };

  const add = useMutation({
    mutationFn: () => addWallet({ chain, address: address.trim() }),
    onSuccess: () => {
      setAddress("");
      invalidate();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteWallet(id),
    onSuccess: invalidate,
  });
  const sync = useMutation({ mutationFn: syncWallets, onSuccess: invalidate });

  const list = wallets.data ?? [];

  return (
    <section className="rounded-lg border border-border bg-card p-6">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
          <WalletIcon className="h-4 w-4" />
          {t("Крипто-кошельки", "Crypto wallets")}
        </h2>
        {list.length > 0 && (
          <Button
            variant="outline"
            disabled={sync.isPending}
            onClick={() => sync.mutate()}
          >
            {sync.isPending
              ? t("Синхронизирую…", "Syncing…")
              : t("Добавить в портфель", "Add to portfolio")}
          </Button>
        )}
      </div>

      <form
        className="mt-4 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (address.trim()) add.mutate();
        }}
      >
        <label className="text-xs text-muted-foreground">
          {t("Сеть", "Network")}
          <select
            className="mt-1 block rounded-md border border-input bg-background px-2 py-2 text-sm"
            value={chain}
            onChange={(e) => setChain(e.target.value as Chain)}
          >
            {CHAINS.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="flex-1 text-xs text-muted-foreground">
          {t("Публичный адрес", "Public address")}
          <Input
            className="mt-1 min-w-[180px]"
            placeholder="bc1q… / 0x… / UQ…"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
          />
        </label>
        <Button type="submit" disabled={add.isPending || !address.trim()}>
          {add.isPending ? "…" : t("Добавить", "Add")}
        </Button>
      </form>
      {add.isError && (
        <p className="mt-2 text-sm text-destructive">
          {t("Не удалось добавить кошелёк.", "Couldn't add the wallet.")}
        </p>
      )}

      <div className="mt-4 space-y-2">
        {list.map((w) => (
          <div
            key={w.id}
            className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm"
          >
            <div className="min-w-0">
              <span className="font-medium">{w.chain}</span>
              <span className="ml-2 text-muted-foreground">
                {w.balance ?? "—"} {w.chain}
              </span>
              <span className="ml-2 block truncate text-xs text-muted-foreground">
                {w.address}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <span className="tabular-nums">{usd(w.usd_value)}</span>
              <button
                type="button"
                className="text-muted-foreground hover:text-red-500"
                onClick={() => remove.mutate(w.id)}
                aria-label={t("Удалить кошелёк", "Delete wallet")}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        ))}
        {list.length === 0 && (
          <p className="text-sm text-muted-foreground">
            {t(
              "Добавьте публичный адрес BTC, ETH или TON — балансы подтянутся автоматически (только чтение).",
              "Add a public BTC, ETH or TON address — balances are fetched automatically (read-only).",
            )}
          </p>
        )}
      </div>

      {sync.isSuccess && (
        <p className="mt-3 text-sm text-emerald-500">
          {t("Снимок создан ·", "Snapshot created ·")} {usd(sync.data.total_usd)}{" "}
          {t("из", "from")} {sync.data.wallet_count} {t("кошельк(ов).", "wallet(s).")}
        </p>
      )}
    </section>
  );
}
