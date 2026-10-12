/**
 * The single country dataset shared by the phone-country-code and nationality pickers, so
 * picking one can set the other via a plain lookup on the same ISO 3166-1 alpha-2 code — never
 * two libraries' independent data models to reconcile.
 *
 * The base list (`react-international-phone`'s `defaultCountries`) already keys `[name, iso2,
 * dialCode]` by lowercase alpha-2 and covers all countries that field needs. `i18n-iso-countries`
 * supplies the zh-TW display name per code; its zh locale follows PRC/UN naming for a few entries
 * that read wrong for a Taiwan-market product (Taiwan itself renders as "中国台湾省"), so those are
 * overridden explicitly below rather than trusted as-is.
 */
import countries from "i18n-iso-countries";
import zhLocale from "i18n-iso-countries/langs/zh.json";
import { defaultCountries as phoneCountries } from "react-international-phone";

countries.registerLocale(zhLocale);

/** Overrides for entries where the upstream zh-TW name doesn't match Taiwan-market usage or
 * cross-strait political framing this product uses elsewhere. */
const ZH_NAME_OVERRIDES: Record<string, string> = {
  TW: "台灣",
  HK: "香港",
  MO: "澳門",
  CN: "中國",
};

export type CountryEntry = {
  /** ISO 3166-1 alpha-2, uppercase — the shared correlation key. */
  iso2: string;
  nameEn: string;
  nameZh: string;
  dialCode: string;
};

export const COUNTRIES: readonly CountryEntry[] = phoneCountries
  .map(([nameEn, iso2Lower, dialCode]): CountryEntry => {
    const iso2 = iso2Lower.toUpperCase();
    const nameZh = ZH_NAME_OVERRIDES[iso2] ?? countries.getName(iso2, "zh") ?? nameEn;
    return { iso2, nameEn, nameZh, dialCode };
  })
  .sort((a, b) => a.nameEn.localeCompare(b.nameEn));
const BY_DIAL_CODE = new Map(COUNTRIES.map((c) => [c.dialCode, c]));

/** Dial codes aren't unique to one country (e.g. `+1` spans the US/Canada/Caribbean) — this
 * returns the dataset's first match, which is the conventional "primary" country for that code. */
export function countryByDialCode(dialCode: string): CountryEntry | undefined {
  return BY_DIAL_CODE.get(dialCode.replace(/^\+/, ""));
}
