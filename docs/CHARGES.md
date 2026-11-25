# Charges table used by the engine

Generated from `engine/src/algora_engine/charges.py` (rates checked 26 Nov 2026). Percentages are of turnover
(premium turnover for options); `txn` and `sebi` apply to buy and sell, `stamp_buy` to buys only, `gst` to
brokerage + txn + SEBI fee. Source keys are defined in the module docstring. `verified=no` means the rate was
taken from a broker's published schedule rather than an exchange/regulator document.

| Segment | Exchange | Component | % | From | To | Source | Verified |
| --- | --- | --- | --- | --- | --- | --- | --- |
| commodity_futures | MCX | stt_buy | 0 | 2024-10-01 | in force | [CTT] | yes |
| commodity_futures | MCX | stt_sell | 0.01 | 2024-10-01 | in force | [CTT] | yes |
| commodity_futures | MCX | txn | 0.0021 | 2024-10-01 | in force | [MCX-TXN] | yes |
| commodity_futures | MCX | sebi | 0.0001 | 2024-10-01 | in force | [SEBI] | yes |
| commodity_futures | MCX | stamp_buy | 0.002 | 2024-10-01 | in force | [STAMP] | yes |
| commodity_futures | MCX | gst | 18 | 2024-10-01 | in force | [GST] | yes |
| commodity_options | MCX | stt_buy | 0 | 2024-10-01 | in force | [CTT] | yes |
| commodity_options | MCX | stt_sell | 0.05 | 2024-10-01 | in force | [CTT] | yes |
| commodity_options | MCX | txn | 0.0418 | 2024-10-01 | in force | [MCX-TXN] | yes |
| commodity_options | MCX | sebi | 0.0001 | 2024-10-01 | in force | [SEBI] | yes |
| commodity_options | MCX | stamp_buy | 0.003 | 2024-10-01 | in force | [STAMP] | no |
| commodity_options | MCX | gst | 18 | 2024-10-01 | in force | [GST] | yes |
| equity_delivery | BSE | stt_buy | 0.1 | 2024-10-01 | in force | [STT-2026] | yes |
| equity_delivery | BSE | stt_sell | 0.1 | 2024-10-01 | in force | [STT-2026] | yes |
| equity_delivery | BSE | txn | 0.00375 | 2024-10-01 | in force | [BSE-TXN] | no |
| equity_delivery | BSE | sebi | 0.0001 | 2024-10-01 | in force | [SEBI] | yes |
| equity_delivery | BSE | stamp_buy | 0.015 | 2024-10-01 | in force | [STAMP] | yes |
| equity_delivery | BSE | gst | 18 | 2024-10-01 | in force | [GST] | yes |
| equity_delivery | NSE | stt_buy | 0.1 | 2024-10-01 | in force | [STT-2026] | yes |
| equity_delivery | NSE | stt_sell | 0.1 | 2024-10-01 | in force | [STT-2026] | yes |
| equity_delivery | NSE | txn | 0.00307 | 2024-10-01 | in force | [NSE-TXN] | yes |
| equity_delivery | NSE | sebi | 0.0001 | 2024-10-01 | in force | [SEBI] | yes |
| equity_delivery | NSE | stamp_buy | 0.015 | 2024-10-01 | in force | [STAMP] | yes |
| equity_delivery | NSE | gst | 18 | 2024-10-01 | in force | [GST] | yes |
| equity_intraday | BSE | stt_buy | 0 | 2024-10-01 | in force | [STT-2026] | yes |
| equity_intraday | BSE | stt_sell | 0.025 | 2024-10-01 | in force | [STT-2026] | yes |
| equity_intraday | BSE | txn | 0.00375 | 2024-10-01 | in force | [BSE-TXN] | no |
| equity_intraday | BSE | sebi | 0.0001 | 2024-10-01 | in force | [SEBI] | yes |
| equity_intraday | BSE | stamp_buy | 0.003 | 2024-10-01 | in force | [STAMP] | yes |
| equity_intraday | BSE | gst | 18 | 2024-10-01 | in force | [GST] | yes |
| equity_intraday | NSE | stt_buy | 0 | 2024-10-01 | in force | [STT-2026] | yes |
| equity_intraday | NSE | stt_sell | 0.025 | 2024-10-01 | in force | [STT-2026] | yes |
| equity_intraday | NSE | txn | 0.00307 | 2024-10-01 | in force | [NSE-TXN] | yes |
| equity_intraday | NSE | sebi | 0.0001 | 2024-10-01 | in force | [SEBI] | yes |
| equity_intraday | NSE | stamp_buy | 0.003 | 2024-10-01 | in force | [STAMP] | yes |
| equity_intraday | NSE | gst | 18 | 2024-10-01 | in force | [GST] | yes |
| futures | BFO | stt_buy | 0 | 2024-10-01 | in force | [STT-2026] | yes |
| futures | BFO | stt_sell | 0.02 | 2024-10-01 | 2026-03-31 | [STT-2024] | yes |
| futures | BFO | stt_sell | 0.05 | 2026-04-01 | in force | [STT-2026] | yes |
| futures | BFO | txn | 0 | 2024-10-01 | in force | [BSE-TXN] | no |
| futures | BFO | sebi | 0.0001 | 2024-10-01 | in force | [SEBI] | yes |
| futures | BFO | stamp_buy | 0.002 | 2024-10-01 | in force | [STAMP] | yes |
| futures | BFO | gst | 18 | 2024-10-01 | in force | [GST] | yes |
| futures | NFO | stt_buy | 0 | 2024-10-01 | in force | [STT-2026] | yes |
| futures | NFO | stt_sell | 0.02 | 2024-10-01 | 2026-03-31 | [STT-2024] | yes |
| futures | NFO | stt_sell | 0.05 | 2026-04-01 | in force | [STT-2026] | yes |
| futures | NFO | txn | 0.00183 | 2024-10-01 | in force | [NSE-TXN] | yes |
| futures | NFO | sebi | 0.0001 | 2024-10-01 | in force | [SEBI] | yes |
| futures | NFO | stamp_buy | 0.002 | 2024-10-01 | in force | [STAMP] | yes |
| futures | NFO | gst | 18 | 2024-10-01 | in force | [GST] | yes |
| options | BFO | stt_buy | 0 | 2024-10-01 | in force | [STT-2026] | yes |
| options | BFO | stt_sell | 0.1 | 2024-10-01 | 2026-03-31 | [STT-2024] | yes |
| options | BFO | stt_sell | 0.15 | 2026-04-01 | in force | [STT-2026] | yes |
| options | BFO | txn | 0.0325 | 2024-10-01 | in force | [BSE-TXN] | no |
| options | BFO | sebi | 0.0001 | 2024-10-01 | in force | [SEBI] | yes |
| options | BFO | stamp_buy | 0.003 | 2024-10-01 | in force | [STAMP] | yes |
| options | BFO | gst | 18 | 2024-10-01 | in force | [GST] | yes |
| options | NFO | stt_buy | 0 | 2024-10-01 | in force | [STT-2026] | yes |
| options | NFO | stt_sell | 0.1 | 2024-10-01 | 2026-03-31 | [STT-2024] | yes |
| options | NFO | stt_sell | 0.15 | 2026-04-01 | in force | [STT-2026] | yes |
| options | NFO | txn | 0.03553 | 2024-10-01 | in force | [NSE-TXN] | yes |
| options | NFO | sebi | 0.0001 | 2024-10-01 | in force | [SEBI] | yes |
| options | NFO | stamp_buy | 0.003 | 2024-10-01 | in force | [STAMP] | yes |
| options | NFO | gst | 18 | 2024-10-01 | in force | [GST] | yes |

Agricultural MCX futures override: CTT 0%, SEBI fee 0.00001% (₹1/crore, broker-published, not verified).

Default brokerage plan `discount-20` (mirrors zerodha.com/charges, 26 Nov 2026): delivery ₹0; intraday, futures and
commodity futures 0.03% or ₹20 per executed order, whichever is lower; options and commodity options ₹20 flat.
