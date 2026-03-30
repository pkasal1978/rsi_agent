"""
Weekly Stock RSI Agent
Fetches current price and 14-day RSI for a list of tickers via yfinance,
then sends a formatted HTML email via Gmail SMTP.
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

import yfinance as yf
import pandas as pd

# ─────────────────────────────────────────────
# ✏️  CONFIGURE YOUR TICKERS HERE
# ─────────────────────────────────────────────
TICKERS = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "TSLA",
    "NVDA",
    "META",
]

# RSI settings
RSI_PERIOD = 14          # standard RSI window
RSI_OVERSOLD = 30        # highlight green below this
RSI_OVERBOUGHT = 70      # highlight red above this


def compute_rsi(series: pd.Series, period: int = 14) -> float:
    """Return the most-recent RSI value for a price series."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)


def fetch_stock_data(tickers: list[str]) -> list[dict]:
    """Download data for all tickers and return a list of row dicts."""
    results = []
    for ticker in tickers:
        try:
            data = yf.download(ticker, period="60d", interval="1d",
                               auto_adjust=True, progress=False)
            if data.empty or len(data) < RSI_PERIOD + 1:
                raise ValueError("Not enough data")

            close = data["Close"].squeeze()
            price = round(float(close.iloc[-1]), 2)
            prev_close = round(float(close.iloc[-2]), 2)
            change_pct = round((price - prev_close) / prev_close * 100, 2)
            rsi = compute_rsi(close, RSI_PERIOD)

            info = yf.Ticker(ticker).fast_info
            company_name = getattr(info, "company_name", ticker) or ticker

            results.append({
                "ticker": ticker,
                "company": company_name,
                "price": price,
                "change_pct": change_pct,
                "rsi": rsi,
            })
        except Exception as e:
            results.append({
                "ticker": ticker,
                "company": ticker,
                "price": "N/A",
                "change_pct": 0,
                "rsi": "N/A",
                "error": str(e),
            })
    return results


def rsi_signal(rsi) -> tuple[str, str]:
    """Return (signal label, hex color) based on RSI value."""
    if rsi == "N/A":
        return "—", "#888888"
    if rsi <= RSI_OVERSOLD:
        return "Oversold 🟢", "#16a34a"
    if rsi >= RSI_OVERBOUGHT:
        return "Overbought 🔴", "#dc2626"
    return "Neutral", "#4b5563"


def build_html(rows: list[dict]) -> str:
    """Build a styled HTML email body."""
    today = datetime.now().strftime("%B %d, %Y")

    table_rows = ""
    for r in rows:
        price_str = f"${r['price']:,.2f}" if isinstance(r["price"], float) else r["price"]
        chg = r["change_pct"]
        chg_color = "#16a34a" if chg >= 0 else "#dc2626"
        chg_str = f"+{chg}%" if chg >= 0 else f"{chg}%"

        rsi_val = r["rsi"]
        signal, sig_color = rsi_signal(rsi_val)
        rsi_display = f"{rsi_val}" if rsi_val != "N/A" else "N/A"

        table_rows += f"""
        <tr>
          <td style="padding:12px 16px;font-weight:600;">{r['ticker']}</td>
          <td style="padding:12px 16px;color:#374151;">{r['company']}</td>
          <td style="padding:12px 16px;font-weight:600;">{price_str}</td>
          <td style="padding:12px 16px;color:{chg_color};font-weight:600;">{chg_str}</td>
          <td style="padding:12px 16px;font-weight:600;">{rsi_display}</td>
          <td style="padding:12px 16px;color:{sig_color};font-weight:600;">{signal}</td>
        </tr>"""

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#f9fafb;margin:0;padding:24px;">
  <div style="max-width:720px;margin:0 auto;background:#ffffff;border-radius:12px;
              box-shadow:0 2px 8px rgba(0,0,0,0.08);overflow:hidden;">

    <!-- Header -->
    <div style="background:#1e3a5f;padding:28px 32px;">
      <h1 style="margin:0;color:#ffffff;font-size:22px;">📈 Weekly Stock RSI Report</h1>
      <p style="margin:6px 0 0;color:#93c5fd;font-size:14px;">{today}</p>
    </div>

    <!-- Table -->
    <div style="padding:24px 16px;">
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <thead>
          <tr style="background:#f1f5f9;color:#6b7280;font-size:12px;text-transform:uppercase;letter-spacing:0.05em;">
            <th style="padding:10px 16px;text-align:left;">Ticker</th>
            <th style="padding:10px 16px;text-align:left;">Company</th>
            <th style="padding:10px 16px;text-align:left;">Price</th>
            <th style="padding:10px 16px;text-align:left;">1D Change</th>
            <th style="padding:10px 16px;text-align:left;">RSI (14)</th>
            <th style="padding:10px 16px;text-align:left;">Signal</th>
          </tr>
        </thead>
        <tbody>
          {table_rows}
        </tbody>
      </table>
    </div>

    <!-- Legend -->
    <div style="padding:0 32px 24px;font-size:12px;color:#9ca3af;">
      <strong>RSI Guide:</strong>
      &nbsp;🟢 Oversold (&lt;{RSI_OVERSOLD}) — potential buy signal &nbsp;|&nbsp;
      🔴 Overbought (&gt;{RSI_OVERBOUGHT}) — potential sell signal
      <br><br>
      <em>Data sourced from Yahoo Finance. This is not financial advice.</em>
    </div>
  </div>
</body>
</html>"""


def send_email(html_body: str):
    """Send the report via Gmail SMTP using environment variables."""
    sender = os.environ["GMAIL_SENDER"]        # your Gmail address
    password = os.environ["GMAIL_APP_PASSWORD"] # Gmail App Password (not login password)
    recipient = os.environ["EMAIL_RECIPIENT"]   # who receives the report

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 Weekly Stock RSI Report — {datetime.now().strftime('%b %d, %Y')}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())

    print(f"✅ Email sent to {recipient}")


def main():
    print("⏳ Fetching stock data...")
    rows = fetch_stock_data(TICKERS)

    for r in rows:
        status = f"  {r['ticker']:6s} | ${r.get('price','N/A')} | RSI {r.get('rsi','N/A')}"
        print(status)

    print("\n📧 Building and sending email...")
    html = build_html(rows)
    send_email(html)


if __name__ == "__main__":
    main()
