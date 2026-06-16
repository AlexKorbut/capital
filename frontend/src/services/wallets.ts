import { api } from "./api";

export type Chain = "BTC" | "ETH" | "TON";

export interface Wallet {
  id: string;
  chain: Chain;
  address: string;
  label: string | null;
  balance: string | null;
  usd_value: string | null;
  created_at: string | null;
}

export interface WalletSyncResult {
  snapshot_id: string;
  total_usd: string | null;
  wallet_count: number;
}

export async function listWallets(): Promise<Wallet[]> {
  const { data } = await api.get<Wallet[]>("/wallets");
  return data;
}

export async function addWallet(body: {
  chain: Chain;
  address: string;
  label?: string;
}): Promise<Wallet> {
  const { data } = await api.post<Wallet>("/wallets", body);
  return data;
}

export async function deleteWallet(id: string): Promise<void> {
  await api.delete(`/wallets/${id}`);
}

export async function syncWallets(): Promise<WalletSyncResult> {
  const { data } = await api.post<WalletSyncResult>("/wallets/sync", {});
  return data;
}
