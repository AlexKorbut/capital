import { api } from "@/services/api";

export async function exportAccount(): Promise<unknown> {
  const { data } = await api.get("/account/export");
  return data;
}

export async function deleteAccount(confirmEmail: string): Promise<void> {
  await api.delete("/account", { data: { confirm_email: confirmEmail } });
}

export async function getNotifications(): Promise<{ emails_enabled: boolean }> {
  const { data } = await api.get("/account/notifications");
  return data;
}

export async function setNotifications(
  emails_enabled: boolean,
): Promise<{ emails_enabled: boolean }> {
  const { data } = await api.put("/account/notifications", { emails_enabled });
  return data;
}

export interface Beneficiary {
  email: string | null;
  days: number;
}

export async function getBeneficiary(): Promise<Beneficiary> {
  const { data } = await api.get<Beneficiary>("/account/beneficiary");
  return data;
}

export async function setBeneficiary(body: Beneficiary): Promise<Beneficiary> {
  const { data } = await api.put<Beneficiary>("/account/beneficiary", body);
  return data;
}

export async function setBaseCurrency(
  base_currency: string,
): Promise<{ base_currency: string }> {
  const { data } = await api.put("/account/base-currency", { base_currency });
  return data;
}

export async function setLanguagePref(lang: string): Promise<{ lang: string }> {
  const { data } = await api.put("/account/language", { lang });
  return data;
}

// --- Personal API tokens (machine clients such as Jarvis) ----------------------

export interface ApiTokenRow {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
}

export interface ApiTokenCreated extends ApiTokenRow {
  /** Plaintext token — shown exactly once, never returned again. */
  token: string;
}

export async function listTokens(): Promise<ApiTokenRow[]> {
  const { data } = await api.get<ApiTokenRow[]>("/account/tokens");
  return data;
}

export async function createToken(name: string): Promise<ApiTokenCreated> {
  const { data } = await api.post<ApiTokenCreated>("/account/tokens", { name });
  return data;
}

export async function revokeToken(id: string): Promise<void> {
  await api.delete(`/account/tokens/${id}`);
}

// --- AI: bring your own keys or use KAPITAL's ---------------------------------

export type AiMode = "kapital" | "own";

export interface AiProvider {
  provider: string;
  label: string;
  configured: boolean;
  hint: string | null;
}

export interface AiSettings {
  mode: AiMode;
  providers: AiProvider[];
  server_ai_available: boolean;
  demo: boolean;
}

export async function getAiSettings(): Promise<AiSettings> {
  const { data } = await api.get<AiSettings>("/account/ai");
  return data;
}

export async function setAiSettings(body: {
  mode?: AiMode;
  keys?: Record<string, string | null>;
}): Promise<AiSettings> {
  const { data } = await api.put<AiSettings>("/account/ai", body);
  return data;
}
