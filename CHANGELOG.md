# Home page review, 26 Nov 2026

## Truth and compliance (fintech pages are judged on this first)
- Paper trading was marked "Available", but no paper engine runs: saved bots always show "Not running". Pipeline step 3 now says "Next"; hero, FAQ and final CTA no longer promise paper trading on live prices.
- Four of five templates have no engine. The "Backtester: Paper only" column became "Status: Backtest ready / Builder only", and the lead no longer says rules are pre-filled.
- "Brokers 12" and "Orders route through your own broker" implied working integrations. Now "Brokers planned 12", "Built for the broker you already use", "Label your broker accounts", and broker buttons read "Add account" instead of "Connect".
- Market section claimed "NSE, BSE and MCX". Data is BSE plus global WTI and gold from TradingView. Title, pills and source line now say so.
- Hero sample used a Nifty lot of 75. NSE cut it to 65 from the Jan 2026 series (NSE/FAOP/70616). Sample trades, KPIs and the backtester default lot are recomputed at 65. Trade 1's exit now matches its own 0.60% target.

## Conversion
- New visitors could not save a bot without first adding a broker label. The builder now offers "Paper only · no broker".
- "Get started" sent people to the broker list. It is now "Build a bot".
- "Your bots at a glance" showed an empty dashboard to every first-time visitor. It now sits under the hero and appears only once the visitor has bots.
- Final CTA's second button goes to the backtester, the thing that works today.
- Added an "Is it free?" FAQ.

## Performance
- Removed 1.6 MB of unused payload: algora_hero.png (1.4 MB, never referenced), hark-hero.css (35 KB with an inline base64 image), hark.js (no matching elements), and the demo grid and trades inside algora-data.js (now algora-logos.js, 69 KB vs 188 KB).
- Fonts: 5 families in 2 render-blocking requests down to 3 families in 1 request (Space Grotesk and IBM Plex Mono were only fallbacks).
- The 5 TradingView iframes in the market section now load only when that section nears the viewport, instead of on first paint.
- When TradingView is blocked (common with ad blockers), panels collapse to a short note instead of ~1,700 px of empty boxes, and the ticker strip hides.

## UX, accessibility and code
- Tabs now have URLs (#dashboard, #bots, #backtest, #brokers): back button, refresh and shared links work.
- Arrow, Home and End keys move between tabs.
- Theme toggle announced "Switch to dark theme" while already dark. Fixed.
- Removed the "Top movers" Gainers/Losers/Most active control: it did nothing.
- Loosened letter-spacing on small Inter Tight headings (FAQ, cards, table) that read as crushed.
- Added favicon and Open Graph tags. Removed dead rolling-word, counter and reveal code.

## Not changed, needs a decision
- Legal: Privacy and Terms are toasts. A trading product needs real Terms, Privacy (DPDP Act 2023) and Risk Disclosure pages before any public launch.
- The hero shows a simulated ₹ P&L. It is labelled, but SEBI bars brokers from associating with platforms that show return claims. Consider a rules-and-trades mock with no rupee totals before approaching broker partners.
- Contact is a phone number only. Add a support email and a grievance contact.
- Sign in is a placeholder toast; real auth is Phase 1 of ACTION_PLAN.md.

## 26 Nov 2026 (b): no demo data, no return figures

Algora is a platform: clients connect, write their own strategy, backtest it, paper trade if they want, then run it live. The home page no longer shows any results or rupee figures.

- Hero: the simulated backtest (net P&L, trades, win rate, drawdown, equity curve, trade rows) and the "Sim feed" price tape are gone. In their place are the example bot's rules in plain English and where the bot goes next (Backtest available, Paper next, Live in development).
- Platform section: removed the simulated equity line from the backtest card.
- Backtester message: "N trades simulated." now reads "N trades in this backtest."
- The TradingView market section stays. Angel One's feed can't be shown to the public from one account, so the Angel One live chart will sit inside the app, on each client's own Angel One login, once the backend exists.
- Cache-bust v=20261126b.

## 26 Nov 2026 (c): Dashboard rebuilt as a trading desk

Researched AlgoTest, Tradetron, Quantiply, uTrade Algos, Dhan, Sensibull and QuantConnect. Everything shown is real: the IST clock, exchange calendars, and what the user saved in this browser. Nothing is simulated.

- Header: a live sentence about the market (open, pre-open, F&O only, MCX open, closed until the next open), an IST clock, and a kill switch that pauses or resumes every bot at once.
- Health lights: Broker API, market data, paper engine and saved data, each stating its real status.
- Today's sessions timeline from 08:30 to 24:00: NSE pre-open and cash, F&O (15:40, 15:45 from 7 Dec 2026), and MCX (23:55 in US winter, 23:30 in US summer; evening only on NSE holidays). Each bot's square-off appears as a tick, and a red now-line moves in real time.
- Six numbers: bots (active and paused), broker accounts, backtests run, next Nifty expiry, next Sensex expiry, and paper P&L (blank until the engine runs).
- Bots table: pause or resume each bot, with a square-off countdown during the session.
- Path to live: a progress ring and checklist (account, bot, backtest, paper, live).
- Risk limits: daily loss limit, max trades a day and stop-entries time. Saved now; the paper engine will enforce them.
- Positions, Orders and Engine log in a terminal panel. Orders carry a signal-to-fill delay column, the most-asked-for missing feature in competitor forums.
- Last backtest: each run of the backtester is now saved (summary plus equity curve). The latest run's curve, metrics and earlier runs show on the dashboard.
- Next two weeks: trading days, weekends, NSE holidays (2026 circulars), Nifty and Sensex weekly and monthly expiries moved for holidays, and the 7 Dec rule change. 2027 weekdays are flagged until NSE publishes its list.
- Activity log: bot saved, paused, resumed or deleted, account added, backtest run, data cleared.
- Broker accounts with their API state and a note on daily session expiry (Kite 6:00 AM, SmartAPI midnight).
- Motion: headline words rise from a mask, labels decode, session bars grow, numbers count up, the ring and equity curve draw in once. Reduced-motion is respected.
- New files: algora-dash.css, algora-dash.js. New saved keys: runs, log, risk (all cleared by "Clear all data").
