# Chip-flow review — output template (level-3 resource)

Load this only when the skill body's inline `output_structure` bullets aren't enough
structure for the caller (e.g. a rich-card renderer needs an explicit section order).

```
## 外資 (Foreign)
- 5-day net: ...
- 10-day net: ...
- 20-day net: ...
- as-of: <trading date>

## 投信/自營 (Investment trust / dealer)
- ...

## 主力/融資券 (Margin & short balance)
- ...

## 籌碼結論 (descriptive only — no buy/sell/hold language)
- concentration or dispersion read, with the reasoning that led there.

<non-trading caveat + currentness caveat, always present>
```
