// Every word on screen, per language. All figures are invented (mock data); "AI server stocks"
// is a sector, not a company. No trading language (web/OVERVIEW.md §4): the copy says
// understand / sources / context, never buy, sell, target or pick; and docs/brand.md §5 (no 漲停,
// 明牌 / hot tips, 目標價; avoid the 盤 root and buy/sell framing such as 買超 / 買盤 / net buying).
export type Lang = 'zh-TW' | 'en';

export type Copy = {
  noiseWords: string[];
  noiseCaption1: string;
  noiseCaption2: string;
  question: string;
  sources: [string, string, string, string];
  convergeCaption: string;
  answerTitle: string;
  answerBadge: string;
  summaryLabel: string;
  summary: string;
  rows: [string, string][];
  doneTitle: string;
  doneMeta: string;
  caveat: string;
  titleWords: string[];
  subtitleWords: string[];
  stem: string;
  pills: string[];
  finale: string;
  composerHint: string;
  wordmark: string;
  tagline: string;
};

// Noise words mix signed moves, headlines and jargon; numbers carry their own sign.
const NOISE_ZH = [
  '外資動向', '+3.2%', '降息？', '爆量', '–1.8%', 'CPI', '財報週', '傳聞', '融資餘額',
  '+0.6%', '量能', '聯準會', '利多？', '–4.1%', 'ETF 換股', '法說會', '殖利率',
  '+2.7%', '處置股', '匯率', '題材', '–0.9%',
];
const NOISE_EN = [
  'Rate cut?', '+3.2%', 'Earnings', 'Volume spike', '–1.8%', 'CPI', 'Rumor', 'Margin',
  '+0.6%', 'Fed', 'Buzz', '–4.1%', 'Outflows', 'Guidance', 'Yields', '+2.7%',
  'Downgrade', 'FX', 'Short interest', '–0.9%', 'Rebalance', 'Hype',
];

export const COPY: Record<Lang, Copy> = {
  'zh-TW': {
    noiseWords: NOISE_ZH,
    noiseCaption1: '市場每天都在說話。',
    noiseCaption2: '但哪些才是重點？',
    question: 'AI 伺服器族群為什麼在漲？',
    sources: ['價量', '籌碼', '新聞', '財報'],
    convergeCaption: 'Miru 一次整理所有資料',
    answerTitle: 'Miru',
    answerBadge: '研究助理',
    summaryLabel: '重點摘要',
    summary: '這波漲勢主要來自法人資金流入與營收成長，\n但估值已高於過去五年平均。', // broken at the comma: no one-character orphan line
    rows: [
      ['外資連續 5 日淨流入', '+NT$4.2B'],
      ['族群營收年增', '+38%'],
      ['成交量為 20 日均量', '1.8×'],
      ['近一週重大訊息', '3 則'],
      ['本益比相對 5 年均值', '偏高'],
    ],
    doneTitle: '分析完成',
    doneMeta: '已比對 4 個資料來源',
    caveat: '僅供研究參考，非投資建議',
    titleWords: ['看懂市場，', '不再靠猜。'],
    subtitleWords: ['每個回答', '都附上', '資料來源', '與脈絡'],
    stem: '問 Miru',
    pills: ['台股', '美股', 'ETF', '產業輪動', '法人籌碼'],
    finale: '任何市場問題。',
    composerHint: '這週 ETF 資金流向哪裡？',
    wordmark: 'Miru',
    tagline: '你的股票與 ETF AI 助理',
  },
  en: {
    noiseWords: NOISE_EN,
    noiseCaption1: 'The market never stops talking.',
    noiseCaption2: 'But what actually matters?',
    question: 'Why are AI server stocks rising?',
    sources: ['Price', 'Flows', 'News', 'Earnings'],
    convergeCaption: 'Miru pulls every source together.',
    answerTitle: 'Miru',
    answerBadge: 'Research assistant',
    summaryLabel: 'Summary',
    summary:
      'The rally is backed by fund inflows and revenue growth, but valuations now sit above their 5-year average.',
    rows: [
      ['Foreign investors, 5 days of net inflows', '+$140M'],
      ['Sector revenue, year over year', '+38%'],
      ['Volume vs. 20-day average', '1.8×'],
      ['Material announcements this week', '3'],
      ['P/E vs. 5-year average', 'Elevated'],
    ],
    doneTitle: 'Analysis complete',
    doneMeta: '4 sources cross-checked',
    caveat: 'Context for research, not investment advice',
    titleWords: ['Understand', 'the', 'market.', 'Stop', 'guessing.'],
    subtitleWords: ['Every', 'answer', 'comes', 'with', 'its', 'sources.'],
    stem: 'Ask Miru about',
    pills: ['Taiwan stocks', 'US stocks', 'ETFs', 'Sector rotation', 'Fund flows'],
    finale: 'the whole market.',
    composerHint: 'Where are ETF flows heading this week?',
    wordmark: 'Miru',
    tagline: 'Your AI assistant for stocks & ETFs',
  },
};
