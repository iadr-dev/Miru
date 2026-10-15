/** Chinese labels for FinMind's `financial_statement` line-item `type` codes (income statement,
 * balance sheet, cash-flow statement) — enumerated empirically by fetching FINANCIALS/
 * BALANCE_SHEET/CASH_FLOW_STATEMENT for a real symbol (`backend/src/sources/finmind/fundamental.py`'s
 * `_make_financial_statement_mapper`, which sets `item` from the raw upstream `type` field
 * verbatim, in FinMind's own CamelCase, with no localized label of its own). Distinct from
 * `field-labels.ts`'s `LABELED_FIELDS` (backend Pydantic field names of a *tool's own* output
 * schema) — these are values of one particular field (`item`), not field names, hence the
 * separate map and lookup function.
 *
 * A `_per` suffix means "as % of revenue" (FinMind's own convention for margin-ratio rows) —
 * handled by stripping the suffix, looking up the base code, and appending "（占營收比）" rather
 * than doubling the dictionary. A code absent from this map falls back to a generic
 * CamelCase-to-spaced-words split (`humanizeCode` below) rather than the raw identifier, so an
 * unmapped code (a symbol-specific line item, or a future FinMind schema addition) still reads
 * as words instead of a crash or gibberish. */

const FINANCIAL_STATEMENT_LABELS: Record<string, string> = {
  // Income statement
  Revenue: "營業收入",
  CostOfGoodsSold: "營業成本",
  GrossProfit: "營業毛利",
  OperatingExpenses: "營業費用",
  OperatingIncome: "營業利益",
  TotalNonoperatingIncomeAndExpense: "營業外收支合計",
  PreTaxIncome: "稅前淨利",
  NetIncomeBeforeTax: "稅前淨利",
  IncomeBeforeIncomeTaxFromContinuingOperations: "繼續營業單位稅前淨利",
  IncomeFromContinuingOperations: "繼續營業單位淨利",
  TAX: "所得稅費用",
  IncomeAfterTaxes: "稅後淨利",
  TotalConsolidatedProfitForThePeriod: "本期綜合損益總額",
  TotalIncomeLossItems: "損益項目總計",
  OtherComprehensiveIncome: "其他綜合損益",
  ComprehensiveIncomeConsolidatedNetIncomeAttributedNonControllingInterest:
    "綜合損益歸屬非控制權益",
  EquityAttributableToOwnersOfParent: "歸屬母公司業主權益",
  NoncontrollingInterests: "非控制權益",
  EPS: "每股盈餘（EPS）",
  InterestExpense: "利息費用",
  InterestIncome: "利息收入",
  OTHNOE: "其他營業外收支",

  // Balance sheet — assets
  CashAndCashEquivalents: "現金及約當現金",
  CurrentFinancialAssetsAtFairvalueThroughProfitOrLoss: "透過損益按公允價值衡量之流動金融資產",
  NonCurrentFinancialAssetsAtFairvalueThroughProfitOrLoss: "透過損益按公允價值衡量之非流動金融資產",
  FinancialAssetsAtFairvalueThroughOtherComprehensiveIncome:
    "透過其他綜合損益按公允價值衡量之金融資產",
  FinancialAssetsAtFairvalueThroughOtherComprehensiveIncomeNonCurrent:
    "透過其他綜合損益按公允價值衡量之非流動金融資產",
  FinancialAssetsAtAmortizedCost: "按攤銷後成本衡量之金融資產",
  FinancialAssetsAtAmortizedCostNonCurrent: "按攤銷後成本衡量之非流動金融資產",
  HedgingAinancialAssets: "避險金融資產",
  AccountsReceivableNet: "應收帳款淨額",
  AccountsReceivableDuefromRelatedPartiesNet: "應收帳款－關係人淨額",
  OtherReceivablesDueFromRelatedParties: "其他應收款－關係人",
  Inventories: "存貨",
  CurrentAssets: "流動資產合計",
  OtherCurrentAssets: "其他流動資產",
  NoncurrentAssets: "非流動資產合計",
  OtherNoncurrentAssets: "其他非流動資產",
  InvestmentAccountedForUsingEquityMethod: "採用權益法之投資",
  PropertyAndPlantAndEquipment: "不動產、廠房及設備",
  PropertyPlantAndEquipment: "不動產、廠房及設備",
  RightOfUseAsset: "使用權資產",
  IntangibleAssets: "無形資產",
  DeferredTaxAssets: "遞延所得稅資產",
  TotalAssets: "資產總計",

  // Balance sheet — liabilities
  CurrentDerivativeFinancialLiabilitiesForHedging: "避險之流動衍生金融負債",
  CurrentFinancialLiabilitiesAtFairValueThroughProfitOrLoss: "透過損益按公允價值衡量之流動金融負債",
  HedgingFinancialLiabilities: "避險金融負債",
  AccountsPayable: "應付帳款",
  AccountsPayableToRelatedParties: "應付帳款－關係人",
  OtherPayables: "其他應付款",
  CurrentTaxLiabilities: "本期所得稅負債",
  CurrentLiabilities: "流動負債合計",
  OtherCurrentLiabilities: "其他流動負債",
  BondsPayable: "應付公司債",
  LongtermBorrowings: "長期借款",
  NoncurrentLiabilities: "非流動負債合計",
  OtherNoncurrentLiabilities: "其他非流動負債",
  Liabilities: "負債總計",

  // Balance sheet — equity
  CapitalStock: "股本",
  OrdinaryShare: "普通股股本",
  CapitalSurplus: "資本公積",
  CapitalSurplusAdditionalPaidInCapital: "資本公積－發行溢價",
  CapitalSurplusDonatedAssetsReceived: "資本公積－受贈資產",
  CapitalSurplusNetAssetsFromMerger: "資本公積－合併溢額",
  CapitalSurplusChangesInEquityOfAssociatesAndJointVenturesAccountedForUsingEquityMethod:
    "資本公積－關聯企業權益變動",
  LegalReserve: "法定盈餘公積",
  RetainedEarnings: "保留盈餘",
  UnappropriatedRetainedEarningsAaccumulatedDeficit: "未分配盈餘（累積虧損）",
  OtherEquityInterest: "其他權益",
  Equity: "權益總額",
  TotalLiabilitiesEquity: "負債及權益總計",
  NumberOfSharesInEntityHeldByEntityAndByItsSubsidiaries: "母公司暨子公司持有本公司股數",

  // Cash flow
  CashFlowsFromOperatingActivities: "營業活動之淨現金流入（流出）",
  NetCashInflowFromOperatingActivities: "營業活動之淨現金流入",
  CashFlowsProvidedFromFinancingActivities: "籌資活動之淨現金流入（流出）",
  CashProvidedByInvestingActivities: "投資活動之淨現金流入（流出）",
  OtherInvestingActivities: "其他投資活動",
  CashReceivedThroughOperations: "營業活動現金收入",
  CashBalancesBeginningOfPeriod: "期初現金餘額",
  CashBalancesEndOfPeriod: "期末現金餘額",
  CashBalancesIncrease: "現金增加數",
  Depreciation: "折舊費用",
  AmortizationExpense: "攤銷費用",
  DecreaseInDepositDeposit: "存出保證金減少",
  InventoryIncrease: "存貨增加",
  ReceivableIncrease: "應收款增加",
  ProceedsFromLongTermDebt: "舉借長期債務",
  RepaymentOfLongTermDebt: "償還長期債務",
  RedemptionOfBonds: "公司債贖回",
  RentalPrincipalRepayments: "租賃本金償還",
  PayTheInterest: "支付利息",
};

/** `PascalCase`/`camelCase` -> spaced words, e.g. `CashAndCashEquivalents` -> "Cash And Cash
 * Equivalents" — the fallback for any code not yet in the dictionary above, so an unmapped or
 * symbol-specific line item never renders as an unbroken run-on identifier. */
function humanizeCode(code: string): string {
  return code
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .trim();
}

const PERCENT_OF_REVENUE_SUFFIX = "_per";

/** Resolves one `financial_statement` `item` code to a label for `locale`. Chinese locales use the
 * dictionary above; every other locale reads the code itself as words (FinMind's codes are English
 * CamelCase, so "OperatingIncome" → "Operating Income"). A trailing `_per` (FinMind's "as % of
 * revenue" marker) is stripped, the base looked up, and the equivalent suffix appended, so
 * `CashAndCashEquivalents_per` needs no dictionary entry of its own. */
export function financialStatementItemLabel(code: string, locale: string): string {
  const chinese = locale.startsWith("zh");
  const label = (c: string): string =>
    (chinese ? FINANCIAL_STATEMENT_LABELS[c] : undefined) ?? humanizeCode(c);
  if (code.endsWith(PERCENT_OF_REVENUE_SUFFIX)) {
    const base = label(code.slice(0, -PERCENT_OF_REVENUE_SUFFIX.length));
    return chinese ? `${base}（占營收比）` : `${base} (% of revenue)`;
  }
  return label(code);
}
