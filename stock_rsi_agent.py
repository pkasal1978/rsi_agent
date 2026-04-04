"""
Weekly Stock & ETF Report Agent
- Prices, Weekly RSI, Monthly RSI, MACD
- Market highlights via RSS feeds (Reuters, CNBC, MarketWatch, Yahoo Finance)
- Separate Stocks and ETFs tables
- Recipients managed via recipients.txt
- Tickers managed via tickers.config
"""

import os
import re
import smtplib
import urllib.request
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate
from datetime import datetime
from pathlib import Path

import yfinance as yf
import pandas as pd


# ──────────────────────────────────────────────
# CONFIG LOADERS
# ──────────────────────────────────────────────

def load_tickers() -> tuple[list[str], list[str]]:
    path = Path(__file__).parent / "tickers.config"
    if not path.exists():
        raise FileNotFoundError("tickers.config not found.")
    stocks, etfs, current_section = [], [], None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        if line == "[STOCKS]":   current_section = "stocks"
        elif line == "[ETFS]":   current_section = "etfs"
        elif current_section == "stocks": stocks.append(line.upper())
        elif current_section == "etfs":   etfs.append(line.upper())
    return stocks, etfs


def load_recipients() -> list[str]:
    path = Path(__file__).parent / "recipients.txt"
    if not path.exists():
        raise FileNotFoundError("recipients.txt not found.")
    recipients = [
        l.strip() for l in path.read_text().splitlines()
        if l.strip() and not l.strip().startswith("#") and "@" in l
    ]
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
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd_line   = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram   = macd_line - signal_line
    macd_val   = round(float(macd_line.iloc[-1]), 3)
    signal_val = round(float(signal_line.iloc[-1]), 3)
    prev_hist  = float(histogram.iloc[-2])
    curr_hist  = float(histogram.iloc[-1])
    if   prev_hist < 0 and curr_hist >= 0: cross = "Bullish Cross 🟢"
    elif prev_hist > 0 and curr_hist <= 0: cross = "Bearish Cross 🔴"
    elif curr_hist > 0:                    cross = "Bullish ↑"
    else:                                  cross = "Bearish ↓"
    return macd_val, signal_val, cross


def fetch_ticker_data(ticker: str) -> dict:
    try:
        daily   = yf.download(ticker, period="6mo", interval="1d",  auto_adjust=True, progress=False)
        weekly  = yf.download(ticker, period="2y",  interval="1wk", auto_adjust=True, progress=False)
        monthly = yf.download(ticker, period="5y",  interval="1mo", auto_adjust=True, progress=False)

        if daily.empty or len(daily) < 30:
            raise ValueError("Not enough daily data")

        dc = daily["Close"].squeeze()
        wc = weekly["Close"].squeeze()  if not weekly.empty  else pd.Series()
        mc = monthly["Close"].squeeze() if not monthly.empty else pd.Series()

        price      = round(float(dc.iloc[-1]), 2)
        prev_close = round(float(dc.iloc[-2]), 2)
        change_pct = round((price - prev_close) / prev_close * 100, 2)
        rsi_w  = compute_rsi(wc, 14) if len(wc) >= 15 else "N/A"
        rsi_m  = compute_rsi(mc, 14) if len(mc) >= 15 else "N/A"
        macd, signal, cross = compute_macd(dc)

        info = yf.Ticker(ticker).fast_info
        name = getattr(info, "company_name", ticker) or ticker

        return {"ticker": ticker, "name": name, "price": price,
                "change_pct": change_pct, "rsi_weekly": rsi_w,
                "rsi_monthly": rsi_m, "macd": macd,
                "macd_signal": signal, "macd_cross": cross}

    except Exception as e:
        print(f"  ⚠️  Error fetching {ticker}: {e}")
        return {"ticker": ticker, "name": ticker, "price": "N/A",
                "change_pct": 0, "rsi_weekly": "N/A", "rsi_monthly": "N/A",
                "macd": "N/A", "macd_signal": "N/A", "macd_cross": "N/A"}


# ──────────────────────────────────────────────
# MARKET HIGHLIGHTS VIA RSS
# ──────────────────────────────────────────────

RSS_SOURCES = [
    # Google News RSS — reliable, never blocked, always fresh
    {"name": "Stock Market",    "url": "https://news.google.com/rss/search?q=stock+market&hl=en-US&gl=US&ceid=US:en"},
    {"name": "S&P 500",         "url": "https://news.google.com/rss/search?q=S%26P500&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Federal Reserve", "url": "https://news.google.com/rss/search?q=federal+reserve+rates&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Nasdaq",          "url": "https://news.google.com/rss/search?q=nasdaq&hl=en-US&gl=US&ceid=US:en"},
]

MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]


def fetch_rss(url: str, source_name: str, max_items: int = 4) -> list[dict]:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; StockRSSBot/1.0)"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read()

        root  = ET.fromstring(content)
        ns    = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)

        results = []
        for item in items[:max_items]:
            title_el = item.find("title") or item.find("atom:title", ns)
            link_el  = item.find("link")  or item.find("atom:link",  ns)
            date_el  = item.find("pubDate") or item.find("atom:published", ns)

            title = (title_el.text or "").strip() if title_el is not None else ""
            link  = (link_el.text  or link_el.get("href", "") or "").strip() if link_el is not None else ""
            pub   = (date_el.text  or "").strip() if date_el is not None else ""

            # Format date nicely
            try:
                parsed = parsedate(pub)
                pub = f"{parsed[2]:02d} {MONTHS[parsed[1]-1]}" if parsed else ""
            except Exception:
                pub = ""

            # Clean CDATA / HTML tags
            title = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', title)
            title = re.sub(r'<[^>]+>', '', title).strip()

            if len(title) > 30:
                results.append({"text": title, "link": link, "date": pub, "source": source_name})

        print(f"  📰 {source_name}: {len(results)} headlines")
        return results

    except Exception as e:
        print(f"  ⚠️  RSS failed for {source_name}: {e}")
        return []


def fetch_market_highlights() -> list[dict]:
    all_headlines = []
    for s in RSS_SOURCES:
        all_headlines.extend(fetch_rss(s["url"], s["name"], max_items=4))
    return all_headlines[:16]


# ──────────────────────────────────────────────
# HTML HELPERS
# ──────────────────────────────────────────────

RSI_OVERSOLD   = 30
RSI_OVERBOUGHT = 70

def rsi_color(rsi) -> str:
    if rsi == "N/A":          return "#6b7280"
    if rsi <= RSI_OVERSOLD:   return "#16a34a"
    if rsi >= RSI_OVERBOUGHT: return "#dc2626"
    return "#1d4ed8"

def macd_color(cross: str) -> str:
    if "Bullish" in cross: return "#16a34a"
    if "Bearish" in cross: return "#dc2626"
    return "#6b7280"


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
        chg_str   = f"+{chg}%" if chg >= 0 else f"{chg}%"
        rsi_w = r["rsi_weekly"];  rsi_m = r["rsi_monthly"]
        macd_val = r["macd"];     sig_val = r["macd_signal"];  cross = r["macd_cross"]
        macd_str = f"{macd_val:.3f}" if isinstance(macd_val, float) else "N/A"
        sig_str  = f"{sig_val:.3f}"  if isinstance(sig_val,  float) else "N/A"

        html += f"""
        <tr style="border-bottom:1px solid #f3f4f6;">
          <td style="padding:11px 12px;font-weight:700;color:#111827;">{r['ticker']}</td>
          <td style="padding:11px 12px;color:#6b7280;font-size:12px;">{r['name'][:28]}</td>
          <td style="padding:11px 12px;font-weight:600;">{price_str}</td>
          <td style="padding:11px 12px;color:{chg_color};font-weight:600;">{chg_str}</td>
          <td style="padding:11px 12px;color:{rsi_color(rsi_w)};font-weight:600;">{rsi_w}</td>
          <td style="padding:11px 12px;color:{rsi_color(rsi_m)};font-weight:600;">{rsi_m}</td>
          <td style="padding:11px 12px;font-size:12px;">{macd_str}</td>
          <td style="padding:11px 12px;font-size:12px;">{sig_str}</td>
          <td style="padding:11px 12px;color:{macd_color(cross)};font-weight:600;font-size:12px;">{cross}</td>
        </tr>"""
    return html


def build_section(title: str, emoji: str, accent: str, rows: list[dict]) -> str:
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
          <tbody>{build_table_rows(rows)}</tbody>
        </table>
      </div>
    </div>"""


def build_highlights_section(headlines: list[dict]) -> str:
    if not headlines:
        return '<div style="padding:16px 24px;color:#9ca3af;font-size:13px;">No headlines available this week.</div>'

    items = ""
    prev_source = None
    for h in headlines:
        if h["source"] != prev_source:
            items += f"""
            <div style="margin-top:16px;margin-bottom:6px;">
              <span style="background:#e0f2fe;color:#0369a1;font-size:10px;font-weight:700;
                           padding:3px 10px;border-radius:999px;text-transform:uppercase;
                           letter-spacing:0.05em;">{h['source']}</span>
            </div>"""
            prev_source = h["source"]

        date_str = f'<span style="color:#9ca3af;font-size:11px;margin-left:6px;">{h["date"]}</span>' if h["date"] else ""
        link_open  = f'<a href="{h["link"]}" style="color:#1d4ed8;text-decoration:none;" target="_blank">' if h["link"] else "<span>"
        link_close = "</a>" if h["link"] else "</span>"

        items += f"""
        <div style="padding:8px 0;border-bottom:1px solid #f3f4f6;font-size:13px;
                    color:#374151;line-height:1.5;">
          • {link_open}{h['text']}{link_close}{date_str}
        </div>"""

    return f"""
    <div style="padding:20px 24px 16px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
        <span style="font-size:18px;">🌐</span>
        <h2 style="margin:0;font-size:15px;font-weight:700;color:#7c3aed;
                   text-transform:uppercase;letter-spacing:0.06em;">Market Highlights</h2>
      </div>
      <p style="margin:0 0 8px;font-size:11px;color:#9ca3af;">
        Sources: Google News (Stock Market · S&amp;P 500 · Federal Reserve · Nasdaq)
      </p>
      {items}
    </div>"""


def build_html(stock_rows, etf_rows, headlines) -> str:
    today   = datetime.now().strftime("%B %d, %Y")
    divider = '<div style="margin:4px 20px;border-top:1px solid #e5e7eb;"></div>'

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:Arial,sans-serif;background:#f3f4f6;margin:0;padding:20px;">
  <div style="max-width:860px;margin:0 auto;background:#ffffff;border-radius:14px;
              box-shadow:0 4px 16px rgba(0,0,0,0.08);overflow:hidden;">

    <div style="background:linear-gradient(135deg,#1e3a5f 0%,#1e40af 100%);padding:28px 32px;">
      <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:800;">
        📈 Weekly Market RSI &amp; MACD Report
      </h1>
      <p style="margin:6px 0 0;color:#93c5fd;font-size:13px;">{today}</p>
    </div>

    <div style="background:#fafafa;padding:10px 24px;font-size:11px;color:#6b7280;
                border-bottom:1px solid #e5e7eb;">
      RSI(W) = Weekly &nbsp;|&nbsp; RSI(M) = Monthly &nbsp;|&nbsp;
      🟢 Oversold &lt;{RSI_OVERSOLD} &nbsp;|&nbsp; 🔴 Overbought &gt;{RSI_OVERBOUGHT} &nbsp;|&nbsp;
      Headlines are clickable links
    </div>

    {build_section("Stocks", "📊", "#1e3a5f", stock_rows)}
    {divider}
    {build_section("ETFs", "🗂️", "#065f46", etf_rows)}
    {divider}
    {build_highlights_section(headlines)}

    <!-- Disclaimer -->
    <div style="margin:0 20px 20px;padding:16px 20px;background:#fffbeb;
                border:1px solid #fcd34d;border-radius:10px;">
      <div style="display:flex;align-items:flex-start;gap:10px;">
        <span style="font-size:20px;line-height:1;">⚠️</span>
        <div>
          <p style="margin:0 0 6px;font-size:13px;font-weight:700;color:#92400e;">
            Educational Purpose Only — Not a Buy/Sell Recommendation
          </p>
          <p style="margin:0;font-size:12px;color:#78350f;line-height:1.6;">
            This report is intended <strong>strictly for educational and informational purposes</strong>.
            The data, indicators (RSI, MACD), and market headlines presented here
            <strong>do not constitute financial advice</strong>, investment recommendations,
            or a solicitation to buy or sell any security or financial instrument.
            Always conduct your own research and consult a qualified financial advisor
            before making any investment decisions.
          </p>
        </div>
      </div>
    </div>

    <!-- Footer -->
    <div style="padding:12px 28px 20px;font-size:11px;color:#9ca3af;background:#fafafa;
                border-top:1px solid #e5e7eb;text-align:center;">
      Data sourced from Yahoo Finance &amp; public RSS feeds &nbsp;·&nbsp;
      Generated on {datetime.now().strftime("%B %d, %Y")}
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

    print("\n🌐 Fetching market highlights via RSS...")
    headlines = fetch_market_highlights()
    print(f"  Total: {len(headlines)} headlines")

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
