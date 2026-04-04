"""
Weekly Stock & ETF Report Agent
- Prices, Weekly RSI, Monthly RSI, MACD
- Market highlights from top financial news sites
- Separate Stocks and ETFs tables
- Recipients managed via recipients.txt
- Tickers managed via tickers.config
"""

import os
import smtplib
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path

import yfinance as yf
import pandas as pd
import urllib.request
from html.parser import HTMLParser


# ──────────────────────────────────────────────
# CONFIG LOADERS
# ──────────────────────────────────────────────

def load_tickers() -> tuple[list[str], list[str]]:
    """Parse tickers.config and return (stocks, etfs)."""
    path = Path(__file__).parent / "tickers.config"
    if not path.exists():
        raise FileNotFoundError("tickers.config not found.")

    stocks, etfs = [], []
    current_section = None

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[STOCKS]":
            current_section = "stocks"
        elif line == "[ETFS]":
            current_section = "etfs"
        elif current_section == "stocks":
            stocks.append(line.upper())
        elif current_section == "etfs":
            etfs.append(line.upper())

    return stocks, etfs


def load_recipients() -> list[str]:
    """Read recipients.txt and return list of email addresses."""
    path = Path(__file__).parent / "recipients.txt"
    if not path.exists():
        raise FileNotFoundError("recipients.txt not found.")

    recipients = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "@" in line:
            recipients.append(line)

    if not recipients:
        raise ValueError("No valid recipients found in recipients.txt")
    return recipients


# ──────────────────────────────────────────────
# INDICATORS
# ──────────────────────────────────────────────

def compute_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)


def compute_macd(series: pd.Series) -> tuple[float, float, str]:
    """Return (MACD line, Signal line, crossover hint)."""
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line

    macd_val   = round(float(macd_line.iloc[-1]), 3)
    signal_val = round(float(signal_line.iloc[-1]), 3)

    # Detect recent crossover (last 2 bars)
    prev_hist = float(histogram.iloc[-2])
    curr_hist = float(histogram.iloc[-1])
    if prev_hist < 0 and curr_hist >= 0:
        cross = "Bullish Cross 🟢"
    elif prev_hist > 0 and curr_hist <= 0:
        cross = "Bearish Cross 🔴"
    elif curr_hist > 0:
        cross = "Bullish ↑"
    else:
        cross = "Bearish ↓"

    return macd_val, signal_val, cross


def fetch_ticker_data(ticker: str) -> dict:
    """Fetch price, weekly RSI, monthly RSI, and MACD for one ticker."""
    try:
        # Daily data for MACD + weekly RSI (needs ~6 months)
        daily = yf.download(ticker, period="6mo", interval="1d",
                            auto_adjust=True, progress=False)
        # Monthly data for monthly RSI
        monthly = yf.download(ticker, period="5y", interval="1mo",
                              auto_adjust=True, progress=False)
        # Weekly data for weekly RSI
        weekly = yf.download(ticker, period="2y", interval="1wk",
                             auto_adjust=True, progress=False)

        if daily.empty or len(daily) < 30:
            raise ValueError("Not enough daily data")

        daily_close  = daily["Close"].squeeze()
        weekly_close = weekly["Close"].squeeze() if not weekly.empty else pd.Series()
        monthly_close = monthly["Close"].squeeze() if not monthly.empty else pd.Series()

        price      = round(float(daily_close.iloc[-1]), 2)
        prev_close = round(float(daily_close.iloc[-2]), 2)
        change_pct = round((price - prev_close) / prev_close * 100, 2)

        rsi_weekly  = compute_rsi(weekly_close, 14) if len(weekly_close) >= 15 else "N/A"
        rsi_monthly = compute_rsi(monthly_close, 14) if len(monthly_close) >= 15 else "N/A"
        macd, signal, cross = compute_macd(daily_close)

        info = yf.Ticker(ticker).fast_info
        name = getattr(info, "company_name", ticker) or ticker

        return {
            "ticker": ticker,
            "name": name,
            "price": price,
            "change_pct": change_pct,
            "rsi_weekly": rsi_weekly,
            "rsi_monthly": rsi_monthly,
            "macd": macd,
            "macd_signal": signal,
            "macd_cross": cross,
        }

    except Exception as e:
        print(f"  ⚠️  Error fetching {ticker}: {e}")
        return {
            "ticker": ticker,
            "name": ticker,
            "price": "N/A",
            "change_pct": 0,
            "rsi_weekly": "N/A",
            "rsi_monthly": "N/A",
            "macd": "N/A",
            "macd_signal": "N/A",
            "macd_cross": "N/A",
        }


# ──────────────────────────────────────────────
# MARKET HIGHLIGHTS SCRAPER
# ──────────────────────────────────────────────

class HeadlineParser(HTMLParser):
    """Simple HTML parser to extract headlines."""
    def __init__(self, tags, class_hints):
        super().__init__()
        self.tags = tags
        self.class_hints = class_hints
        self.headlines = []
        self._capture = False
        self._current = ""

    def handle_starttag(self, tag, attrs):
        if tag in self.tags:
            attrs_dict = dict(attrs)
            cls = attrs_dict.get("class", "")
            if any(h in cls for h in self.class_hints):
                self._capture = True
                self._current = ""

    def handle_data(self, data):
        if self._capture:
            self._current += data

    def handle_endtag(self, tag):
        if self._capture and tag in self.tags:
            text = self._current.strip()
            if len(text) > 30:
                self.headlines.append(text)
            self._capture = False


def scrape_headlines(url: str, tags: list, class_hints: list,
                     max_items: int = 5, source_name: str = "") -> list[dict]:
    """Fetch headlines from a URL using simple HTML parsing."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        parser = HeadlineParser(tags, class_hints)
        parser.feed(html)

        seen = set()
        results = []
        for h in parser.headlines:
            h = re.sub(r'\s+', ' ', h).strip()
            if h not in seen and len(h) > 40:
                seen.add(h)
                results.append({"text": h, "source": source_name})
            if len(results) >= max_items:
                break
        return results

    except Exception as e:
        print(f"  ⚠️  Could not fetch headlines from {source_name}: {e}")
        return []


def fetch_market_highlights() -> list[dict]:
    """Aggregate market headlines from multiple financial news sources."""
    all_headlines = []

    sources = [
        {
            "name": "Reuters",
            "url": "https://www.reuters.com/finance/",
            "tags": ["h3", "a"],
            "hints": ["story-title", "heading__", "article-heading"],
        },
        {
            "name": "MarketWatch",
            "url": "https://www.marketwatch.com/latest-news",
            "tags": ["h3", "a"],
            "hints": ["article__headline", "link--title"],
        },
        {
            "name": "Investing.com",
            "url": "https://www.investing.com/news/stock-market-news",
            "tags": ["a"],
            "hints": ["title", "articleItem"],
        },
    ]

    for s in sources:
        headlines = scrape_headlines(
            s["url"], s["tags"], s["hints"],
            max_items=4, source_name=s["name"]
        )
        all_headlines.extend(headlines)

    return all_headlines[:12]


# ──────────────────────────────────────────────
# RSI HELPERS
# ──────────────────────────────────────────────

RSI_OVERSOLD   = 30
RSI_OVERBOUGHT = 70


def rsi_color(rsi) -> str:
    if rsi == "N/A": return "#6b7280"
    if rsi <= RSI_OVERSOLD:   return "#16a34a"
    if rsi >= RSI_OVERBOUGHT: return "#dc2626"
    return "#1d4ed8"


def macd_color(cross: str) -> str:
    if "Bullish" in cross: return "#16a34a"
    if "Bearish" in cross: return "#dc2626"
    return "#6b7280"


# ──────────────────────────────────────────────
# HTML BUILDER
# ──────────────────────────────────────────────

THEAD = """
<thead>
  <tr style="background:#f1f5f9;color:#6b7280;font-size:11px;
             text-transform:uppercase;letter-spacing:0.05em;">
    <th style="padding:10px 12px;text-align:left;">Ticker</th>
    <th style="padding:10px 12px;text-align:left;">Name</th>
    <th style="padding:10px 12px;text-align:left;">Price</th>
    <th style="padding:10px 12px;text-align:left;">1D Chg</th>
    <th style="padding:10px 12px;text-align:left;">RSI (W)</th>
    <th style="padding:10px 12px;text-align:left;">RSI (M)</th>
    <th style="padding:10px 12px;text-align:left;">MACD</th>
    <th style="padding:10px 12px;text-align:left;">Signal</th>
    <th style="padding:10px 12px;text-align:left;">MACD Cross</th>
  </tr>
</thead>"""


def build_table_rows(rows: list[dict]) -> str:
    html = ""
    for r in rows:
        price_str = f"${r['price']:,.2f}" if isinstance(r["price"], float) else "N/A"
        chg = r["change_pct"]
        chg_color = "#16a34a" if chg >= 0 else "#dc2626"
        chg_str = f"+{chg}%" if chg >= 0 else f"{chg}%"

        rsi_w = r["rsi_weekly"]
        rsi_m = r["rsi_monthly"]
        macd_val = r["macd"]
        sig_val  = r["macd_signal"]
        cross    = r["macd_cross"]

        macd_str = f"{macd_val:.3f}" if isinstance(macd_val, float) else "N/A"
        sig_str  = f"{sig_val:.3f}"  if isinstance(sig_val, float)  else "N/A"
        rsi_w_str = f"{rsi_w}" if rsi_w != "N/A" else "N/A"
        rsi_m_str = f"{rsi_m}" if rsi_m != "N/A" else "N/A"

        html += f"""
        <tr style="border-bottom:1px solid #f3f4f6;">
          <td style="padding:11px 12px;font-weight:700;color:#111827;">{r['ticker']}</td>
          <td style="padding:11px 12px;color:#6b7280;font-size:12px;">{r['name'][:28]}</td>
          <td style="padding:11px 12px;font-weight:600;">{price_str}</td>
          <td style="padding:11px 12px;color:{chg_color};font-weight:600;">{chg_str}</td>
          <td style="padding:11px 12px;color:{rsi_color(rsi_w)};font-weight:600;">{rsi_w_str}</td>
          <td style="padding:11px 12px;color:{rsi_color(rsi_m)};font-weight:600;">{rsi_m_str}</td>
          <td style="padding:11px 12px;font-size:12px;">{macd_str}</td>
          <td style="padding:11px 12px;font-size:12px;">{sig_str}</td>
          <td style="padding:11px 12px;color:{macd_color(cross)};font-weight:600;font-size:12px;">{cross}</td>
        </tr>"""
    return html


def build_section(title: str, emoji: str, accent: str, rows: list[dict]) -> str:
    table_rows = build_table_rows(rows)
    return f"""
    <div style="padding:24px 20px 12px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">
        <span style="font-size:18px;">{emoji}</span>
        <h2 style="margin:0;font-size:15px;font-weight:700;color:{accent};
                   text-transform:uppercase;letter-spacing:0.06em;">{title}</h2>
      </div>
      <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          {THEAD}
          <tbody>{table_rows}</tbody>
        </table>
      </div>
    </div>"""


def build_highlights_section(headlines: list[dict]) -> str:
    if not headlines:
        return ""

    items = ""
    prev_source = None
    for h in headlines:
        source = h["source"]
        source_badge = ""
        if source != prev_source:
            source_badge = f"""
            <div style="margin-top:14px;margin-bottom:4px;">
              <span style="background:#e0f2fe;color:#0369a1;font-size:10px;font-weight:700;
                           padding:2px 8px;border-radius:999px;text-transform:uppercase;
                           letter-spacing:0.05em;">{source}</span>
            </div>"""
            prev_source = source

        items += f"""
        {source_badge}
        <div style="padding:8px 0;border-bottom:1px solid #f3f4f6;font-size:13px;color:#374151;line-height:1.5;">
          • {h['text']}
        </div>"""

    return f"""
    <div style="padding:20px 24px 16px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
        <span style="font-size:18px;">🌐</span>
        <h2 style="margin:0;font-size:15px;font-weight:700;color:#7c3aed;
                   text-transform:uppercase;letter-spacing:0.06em;">Market Highlights</h2>
      </div>
      {items}
      <p style="margin-top:10px;font-size:11px;color:#9ca3af;">
        Sources: Reuters · MarketWatch · Investing.com
      </p>
    </div>"""


def build_html(stock_rows: list[dict], etf_rows: list[dict],
               headlines: list[dict]) -> str:
    today = datetime.now().strftime("%B %d, %Y")

    stocks_html     = build_section("Stocks", "📊", "#1e3a5f", stock_rows)
    etfs_html       = build_section("ETFs",   "🗂️",  "#065f46", etf_rows)
    highlights_html = build_highlights_section(headlines)

    divider = '<div style="margin:4px 20px;border-top:1px solid #e5e7eb;"></div>'

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:Arial,sans-serif;background:#f3f4f6;margin:0;padding:20px;">
  <div style="max-width:860px;margin:0 auto;background:#ffffff;border-radius:14px;
              box-shadow:0 4px 16px rgba(0,0,0,0.08);overflow:hidden;">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#1e3a5f 0%,#1e40af 100%);padding:28px 32px;">
      <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:800;">
        📈 Weekly Market RSI &amp; MACD Report
      </h1>
      <p style="margin:6px 0 0;color:#93c5fd;font-size:13px;">{today}</p>
    </div>

    <!-- Legend bar -->
    <div style="background:#fafafa;padding:10px 24px;font-size:11px;color:#6b7280;
                border-bottom:1px solid #e5e7eb;">
      RSI(W) = Weekly &nbsp;|&nbsp; RSI(M) = Monthly &nbsp;|&nbsp;
      🟢 Oversold &lt;{RSI_OVERSOLD} &nbsp;|&nbsp; 🔴 Overbought &gt;{RSI_OVERBOUGHT} &nbsp;|&nbsp;
      MACD Cross = recent bullish/bearish signal
    </div>

    {stocks_html}
    {divider}
    {etfs_html}
    {divider}
    {highlights_html}

    <!-- Footer -->
    <div style="padding:16px 28px 24px;font-size:11px;color:#9ca3af;background:#fafafa;
                border-top:1px solid #e5e7eb;">
      Data sourced from Yahoo Finance &amp; public financial news sites.
      This report is for informational purposes only and does not constitute financial advice.
    </div>
  </div>
</body>
</html>"""


# ──────────────────────────────────────────────
# EMAIL SENDER
# ──────────────────────────────────────────────

def send_email(html_body: str, recipients: list[str]):
    sender   = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]

    for recipient in recipients:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"📈 Weekly Market Report — {datetime.now().strftime('%b %d, %Y')}"
            msg["From"]    = sender
            msg["To"]      = recipient
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(sender, password)
                server.sendmail(sender, recipient, msg.as_string())

            print(f"  ✅ Sent to {recipient}")
        except Exception as e:
            print(f"  ❌ Failed to send to {recipient}: {e}")


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

def main():
    stocks, etfs = load_tickers()

    print(f"⏳ Fetching {len(stocks)} stocks...")
    stock_rows = [fetch_ticker_data(t) for t in stocks]

    print(f"\n⏳ Fetching {len(etfs)} ETFs...")
    etf_rows = [fetch_ticker_data(t) for t in etfs]

    print("\n🌐 Fetching market highlights...")
    headlines = fetch_market_highlights()
    print(f"  Found {len(headlines)} headlines")

    print("\n📋 Loading recipients...")
    recipients = load_recipients()
    for r in recipients:
        print(f"  → {r}")

    print("\n📧 Building and sending email...")
    html = build_html(stock_rows, etf_rows, headlines)
    send_email(html, recipients)
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
