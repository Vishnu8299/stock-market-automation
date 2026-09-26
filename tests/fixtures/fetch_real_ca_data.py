"""
Fetch real NSE corporate-action price data and save as test fixtures.
M0.2.3-B Real Corporate-Action Validation.
"""
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

import yfinance as yf
import pandas as pd

fixture_dir = "tests/fixtures/corporate_actions"
os.makedirs(fixture_dir, exist_ok=True)

# ════════════════════════════════════════════════════════════════
# EVENT A: IRCTC 5:1 Stock Split (ex-date 2021-10-29)
# ════════════════════════════════════════════════════════════════
print("Fetching IRCTC.NS (5:1 split)...")
irctc = yf.download('IRCTC.NS', start='2021-10-20', end='2021-11-10', auto_adjust=False)
irctc_df = irctc.reset_index()
# Flatten MultiIndex columns if present
if isinstance(irctc_df.columns, pd.MultiIndex):
    irctc_df.columns = irctc_df.columns.droplevel(1)
irctc_df['Date'] = pd.to_datetime(irctc_df['Date']).dt.date
out = irctc_df[['Date','Open','High','Low','Close','Adj Close','Volume']].copy()
out.columns = ['trading_date','open','high','low','close','adj_close','volume']
out.to_csv(f"{fixture_dir}/irctc_split_2021.csv", index=False)
print(f"  Saved {len(out)} rows")

# Reconstruct RAW (pre-adjustment) prices for pre-split dates
# yfinance Close is already split-adjusted: 826 * 5 = 4130 (known NSE price)
raw_irctc = out.copy()
split_exdate = pd.Timestamp('2021-10-29').date()
for col in ['open','high','low','close']:
    raw_irctc.loc[raw_irctc['trading_date'] < split_exdate, col] = (
        raw_irctc.loc[raw_irctc['trading_date'] < split_exdate, col] * 5
    )
raw_irctc.to_csv(f"{fixture_dir}/irctc_split_2021_raw.csv", index=False)
print(f"  Saved RAW reconstruction: {len(raw_irctc)} rows")

# Print key dates
print("  Key prices (yfinance adjusted):")
for _, row in out.iterrows():
    marker = ""
    if str(row['trading_date']) == "2021-10-28":
        marker = " <-- last pre-split day"
    elif str(row['trading_date']) == "2021-10-29":
        marker = " <-- SPLIT EX-DATE"
    elif str(row['trading_date']) == "2021-11-01":
        marker = " <-- first post-split day"
    print(f"    {row['trading_date']}  C={row['close']:>10.2f}  AC={row['adj_close']:>10.2f}{marker}")

# ════════════════════════════════════════════════════════════════
# EVENT B: TCS 1:1 Bonus (ex-date 2018-06-14 per NSE)
# ════════════════════════════════════════════════════════════════
print("\nFetching TCS.NS (1:1 bonus)...")
tcs = yf.download('TCS.NS', start='2018-05-25', end='2018-06-22', auto_adjust=False)
tcs_df = tcs.reset_index()
if isinstance(tcs_df.columns, pd.MultiIndex):
    tcs_df.columns = tcs_df.columns.droplevel(1)
tcs_df['Date'] = pd.to_datetime(tcs_df['Date']).dt.date
out2 = tcs_df[['Date','Open','High','Low','Close','Adj Close','Volume']].copy()
out2.columns = ['trading_date','open','high','low','close','adj_close','volume']
out2.to_csv(f"{fixture_dir}/tcs_bonus_2018.csv", index=False)
print(f"  Saved {len(out2)} rows")

# yfinance reports the bonus as a "split" event on 2018-05-31
# But NSE ex-date was 2018-06-14. yfinance Close is already adjusted.
# NOTE: yfinance dates the split event differently from NSE.
# We use the NSE authoritative ex-date for our CorporateAction.
bonus_exdate = pd.Timestamp('2018-06-14').date()
yf_split_date = pd.Timestamp('2018-05-31').date()

# Reconstruct RAW: multiply pre-bonus Close by 2
raw_tcs = out2.copy()
for col in ['open','high','low','close']:
    raw_tcs.loc[raw_tcs['trading_date'] < bonus_exdate, col] = (
        raw_tcs.loc[raw_tcs['trading_date'] < bonus_exdate, col] * 2
    )
raw_tcs.to_csv(f"{fixture_dir}/tcs_bonus_2018_raw.csv", index=False)
print(f"  Saved RAW reconstruction: {len(raw_tcs)} rows")

print("  Key prices (yfinance adjusted):")
for _, row in out2.iterrows():
    marker = ""
    if str(row['trading_date']) == "2018-05-31":
        marker = " <-- yfinance split date"
    elif str(row['trading_date']) == "2018-06-13":
        marker = " <-- last pre-bonus day"
    elif str(row['trading_date']) == "2018-06-14":
        marker = " <-- NSE BONUS EX-DATE"
    print(f"    {row['trading_date']}  C={row['close']:>10.2f}  AC={row['adj_close']:>10.2f}{marker}")

# ════════════════════════════════════════════════════════════════
# EVENT C: INFY Final Dividend Rs.17.50 (ex-date 2023-06-02)
# ════════════════════════════════════════════════════════════════
print("\nFetching INFY.NS (dividend)...")
infy = yf.download('INFY.NS', start='2023-05-29', end='2023-06-09', auto_adjust=False)
infy_df = infy.reset_index()
if isinstance(infy_df.columns, pd.MultiIndex):
    infy_df.columns = infy_df.columns.droplevel(1)
infy_df['Date'] = pd.to_datetime(infy_df['Date']).dt.date
out3 = infy_df[['Date','Open','High','Low','Close','Adj Close','Volume']].copy()
out3.columns = ['trading_date','open','high','low','close','adj_close','volume']
out3.to_csv(f"{fixture_dir}/infy_dividend_2023.csv", index=False)
print(f"  Saved {len(out3)} rows")

print("  Key prices:")
for _, row in out3.iterrows():
    diff = row['close'] - row['adj_close']
    marker = " <-- DIVIDEND EX-DATE" if str(row['trading_date']) == "2023-06-02" else ""
    print(f"    {row['trading_date']}  C={row['close']:>10.2f}  AC={row['adj_close']:>10.2f}  diff={diff:>8.2f}{marker}")

# Also fetch dividend data from Ticker
infy_ticker = yf.Ticker('INFY.NS')
divs = infy_ticker.dividends
print(f"\n  INFY dividends in 2023:")
for d, v in divs.items():
    if d.year == 2023:
        print(f"    {d.date()}: Rs.{v:.2f}")

print("\n" + "=" * 60)
print("DATA SOURCING FINDING:")
print("  yfinance auto_adjust=False Close is ALREADY adjusted for")
print("  splits/bonuses. Only Adj Close further adjusts for dividends.")
print("  RAW fixtures reconstructed by reversing known adjustment.")
print("=" * 60)
print("\nDONE")
