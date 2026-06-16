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
