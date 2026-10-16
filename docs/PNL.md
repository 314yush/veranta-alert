# Position and alert calculations

Gross PnL = (mark - entry) / entry × collateral × leverage × direction, where direction is +1 for longs and -1 for shorts. PnL percentage divides dollar PnL by collateral and multiplies by 100.

The reported BTC example gives (81589.48 - 81614.22) / 81614.22 × 10 × 182.02 = -$0.55176, or -5.5176% gross. This explains the displayed rounding, but does not independently prove accrued fees were zero.

Net PnL uses the official SDK `position_net_pnl`. Upside deducts profit share on positive gross profits, rollover and signed funding. Negative funding is a credit. Regular positions deduct closing fees, rollover and funding, with applicable loss protection. SDK default closing fees use notional; exchange displays using notional plus PnL can differ slightly. Missing fee metadata or a failed computation produces unavailable net PnL, never a gross number labelled net.

Liquidation warnings use the position API's liquidation price. Remaining collateral points = (mark - liquidation) × direction / entry × leverage × 100. Default warnings fire at 45, 25 and 10 points remaining. Existing stored preset values retain their equivalent distance mapping. TP/SL proximity measures leveraged price distance in collateral points, not a promise of realized net proceeds. Nonfinite prices are rejected for risk calculations.

A vanished position is reported closed without guessing TP, SL, liquidation, or realized PnL. During this audit the wallet trade-history endpoint returned an upstream failure; final execution accounting remains unverified. Fifteen-second polling and delivery are best effort, especially for highly leveraged positions.

Charts try Veranta candles, then clearly labelled reference candles for a regular market sharing the verified price feed, then locally observed ticks. Stale or malformed candles are rejected. Reference prices are illustrative; position levels remain Veranta values. Without history, current levels still render. Distant targets use explicit off-scale markers to preserve candle readability.

Sources: https://sdk.veranta.xyz/data/compute.md and https://sdk.veranta.xyz/data/markets.md .
