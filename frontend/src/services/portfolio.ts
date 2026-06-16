import { api } from "./api";
import { useLang } from "@/lib/i18n";

export type AssetType =
  | "cash"
  | "bank_deposit"
  | "crypto"
  | "stock"
  | "real_estate"
  | "vehicle"
  | "debt"
  | "other";

// Amounts arrive as strings/numbers (backend uses Decimal). Keep them as
// strings on the wire to avoid float loss; UI renders them verbatim.
export interface AssetItem {
  asset_type: AssetType;
  amount: string | number;
  currency: string;
  country?: string | null;
  location?: string | null;
  note?: string | null;
  ticker?: string | null;
  symbol?: string | null;
  quantity?: string | number | null;
  interest_rate?: string | number | null;
  wallet_address?: string | null;
  counterparty?: string | null;
  is_owed_to_me?: boolean | null;
  appreciation_rate?: string | number | null;
  usd_value?: string | number | null;
  usd_rate?: string | number | null;
  confidence: number;
}

export interface ValidationResult {
  is_valid: boolean;
  needs_review: boolean;
  errors: string[];
  warnings: string[];
}

export interface PreviewResponse {
  thread_id: string;
  assets: AssetItem[];
  validation?: ValidationResult | null;
  needs_review: boolean;
  total_usd?: string | null;
}

export interface ConfirmResponse {
  snapshot_id: string;
  assets: AssetItem[];
  total_usd?: string | null;
}

export async function submitInput(body: {
  text: string;
  base_currency?: string;
}): Promise<PreviewResponse> {
  const { data } = await api.post<PreviewResponse>("/portfolio/input", {
    ...body,
    input_type: "text",
  });
  return data;
}

export async function confirmInput(body: {
  thread_id: string;
  assets?: AssetItem[];
}): Promise<ConfirmResponse> {
  const { data } = await api.post<ConfirmResponse>("/portfolio/confirm", body);
  return data;
}

export type UploadKind = "voice" | "file" | "image";

export async function uploadInput(
  file: Blob,
  inputType: UploadKind,
  filename?: string,
  baseCurrency?: string,
): Promise<PreviewResponse> {
  const form = new FormData();
  form.append("file", file, filename ?? "upload");
  form.append("input_type", inputType);
  if (baseCurrency) form.append("base_currency", baseCurrency);
  const { data } = await api.post<PreviewResponse>(
    "/portfolio/input/upload",
    form,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return data;
}

export async function importTable(
  file: Blob,
  filename?: string,
  baseCurrency?: string,
): Promise<PreviewResponse> {
  const form = new FormData();
  form.append("file", file, filename ?? "import.csv");
  if (baseCurrency) form.append("base_currency", baseCurrency);
  const { data } = await api.post<PreviewResponse>("/portfolio/import", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function downloadImportTemplate(): Promise<void> {
  const res = await api.get("/portfolio/import/template", {
    responseType: "blob",
  });
  const url = URL.createObjectURL(res.data as Blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "kapital-template.csv";
  a.click();
  URL.revokeObjectURL(url);
}

// --- Read side (dashboard) ----------------------------------------------------

export interface BreakdownEntry {
  key: string;
  usd_value: string | null;
}

export interface Breakdown {
  by_type: BreakdownEntry[];
  by_currency: BreakdownEntry[];
  by_country: BreakdownEntry[];
}

export interface AssetRow {
  id: string;
  asset_type: AssetType;
  amount: string | null;
  currency: string | null;
  country: string | null;
  location: string | null;
  usd_value: string | null;
  usd_rate: string | null;
  symbol: string | null;
  ticker: string | null;
  quantity: string | null;
  interest_rate: string | null;
  appreciation_rate: string | null;
  estimated_usd: string | null;
  is_owed_to_me: boolean | null;
}

export interface CurrentPortfolio {
  snapshot_id: string;
  created_at: string | null;
  total_usd: string | null;
  estimated_total_usd: string | null;
  base_currency: string | null;
  usd_per_base: string | null;
  assets: AssetRow[];
  breakdown: Breakdown;
}

export interface HistoryPoint {
  date: string | null;
  total_usd: string | null;
}

export interface ReturnWindow {
  key: string;
  label: string;
  baseline_usd: string | null;
  baseline_date: string | null;
  change_usd: string | null;
  change_pct: string | null;
  partial: boolean;
}

export interface ReturnsResponse {
  current_usd: string | null;
  as_of: string | null;
  snapshots_count: number;
  span_days: number;
  first_date: string | null;
  cagr_pct: string | null;
  windows: ReturnWindow[];
}

export async function getCurrentPortfolio(): Promise<CurrentPortfolio | null> {
  const { data } = await api.get<CurrentPortfolio | null>("/portfolio/current");
  return data;
}

export interface AssetUpsert {
  asset_type: AssetType;
  amount: number;
  currency?: string;
  location?: string | null;
  country?: string | null;
  ticker?: string | null;
  symbol?: string | null;
  interest_rate?: number | null;
  appreciation_rate?: number | null;
}

export async function addAsset(body: AssetUpsert): Promise<CurrentPortfolio> {
  const { data } = await api.post<CurrentPortfolio>("/portfolio/assets", body);
  return data;
}

export async function updateAsset(
  id: string,
  body: AssetUpsert,
): Promise<CurrentPortfolio> {
  const { data } = await api.patch<CurrentPortfolio>(
    `/portfolio/assets/${id}`,
    body,
  );
  return data;
}

export async function deleteAsset(id: string): Promise<CurrentPortfolio> {
  const { data } = await api.delete<CurrentPortfolio>(`/portfolio/assets/${id}`);
  return data;
}

export async function getHistoryChart(): Promise<HistoryPoint[]> {
  const { data } = await api.get<HistoryPoint[]>("/portfolio/history/chart");
  return data;
}

export async function getReturns(): Promise<ReturnsResponse | null> {
  const { data } = await api.get<ReturnsResponse | null>("/portfolio/returns");
  return data;
}

export interface AllocationRow {
  asset_type: AssetType;
  current_pct: string;
  target_pct: string | null;
  drift_pct: string | null;
}

export interface AllocationResponse {
  has_target: boolean;
  rows: AllocationRow[];
}

export async function getAllocation(): Promise<AllocationResponse> {
  const { data } = await api.get<AllocationResponse>("/portfolio/allocation");
  return data;
}

export async function setAllocation(
  targets: Record<string, number>,
): Promise<AllocationResponse> {
  const { data } = await api.put<AllocationResponse>("/portfolio/allocation", {
    targets,
  });
  return data;
}

export const ASSET_TYPE_LABELS: Record<AssetType, string> = {
  cash: "Наличные",
  bank_deposit: "Депозит",
  crypto: "Криптовалюта",
  stock: "Акции",
  real_estate: "Недвижимость",
  vehicle: "Авто",
  debt: "Долг",
  other: "Другое",
};

export const ASSET_TYPE_LABELS_EN: Record<AssetType, string> = {
  cash: "Cash",
  bank_deposit: "Deposit",
  crypto: "Crypto",
  stock: "Stocks",
  real_estate: "Real estate",
  vehicle: "Vehicle",
  debt: "Debt",
  other: "Other",
};

/** Reactive asset-type label map for the current language. */
export function useAssetLabels(): Record<AssetType, string> {
  return useLang((s) => s.lang) === "en"
    ? ASSET_TYPE_LABELS_EN
    : ASSET_TYPE_LABELS;
}
