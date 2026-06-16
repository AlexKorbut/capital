import { api } from "@/services/api";

export interface LegalDoc {
  slug: string;
  title: string;
  markdown: string;
}

export async function getLegal(slug: string, lang = "ru"): Promise<LegalDoc> {
  const { data } = await api.get<LegalDoc>(`/legal/${slug}`, { params: { lang } });
  return data;
}
