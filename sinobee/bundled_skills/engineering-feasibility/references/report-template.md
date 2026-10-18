# report.md template

Write in the user's language. Headings in this order (the check looks for them; the English words work too).

```markdown
# <subject>

> 判定：**技術可行 | 有條件可行 | 技術不可行**。<one sentence with the deciding number>
> 互動 3D 解剖頁：<link to the published page>

## 判定 (Verdict)
3–5 sentences: what works, what decides it, the envelope (seconds, cycles, surfaces) or the law that breaks it.

## 主張與檢驗 (Claims)
| # | 主張 | 來源 | 需求 | 物理極限 | 餘裕 | 狀態 |
|---|---|---|---|---|---|---|
| C1 | … | [video 12:40](https://www.youtube.com/watch?v=ID&t=760s) | 5 s 懸停 | 4.3 s | 0.86× | 有條件 |

## 工程原理 (How it works)
One subsection per mechanism (thruster, storage, suction…): the physics in plain words, the key formula, the
numbers from the model, and which figure shows it.

## 物理極限 (Physical limits)
The ledger: each limit with a number and what it stops.

## 實際運行模式 (Operating envelope)
How the system really operates within the limits (e.g. ballistic hops with pulsed attitude control and a braking
burn; one stunt per fill). For an infeasible verdict: what it would take (the requirement scaled to physics: mass,
energy, materials) and how far that is from today.

## 假設 (Assumptions)
Every unpublished input, its value and why.

## 來源 (Sources)
Numbered list with URL and date. Mark what is a claim by the company and what is independent.
```
