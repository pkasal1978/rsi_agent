# 📈 Weekly Stock RSI Agent

A GitHub Actions bot that emails you a styled weekly report of stock prices and RSI (Relative Strength Index) every Sunday — no server required.

---

## 📧 Sample Email Output

| Ticker | Company       | Price     | 1D Change | RSI (14) | Signal        |
|--------|---------------|-----------|-----------|----------|---------------|
| AAPL   | Apple Inc.    | $213.49   | +0.82%    | 58.3     | Neutral       |
| NVDA   | NVIDIA Corp.  | $875.00   | +2.1%     | 72.4     | Overbought 🔴 |
| TSLA   | Tesla Inc.    | $172.30   | -1.4%     | 28.1     | Oversold 🟢   |

---

## 🚀 Setup Guide

### 1. Fork / Clone this repo
```bash
git clone https://github.com/YOUR_USERNAME/stock-rsi-agent.git
cd stock-rsi-agent
```

### 2. Customize your tickers
Open `stock_rsi_agent.py` and edit the `TICKERS` list at the top:
```python
TICKERS = [
    "AAPL",
    "TSLA",
    "NVDA",
    # Add any valid Yahoo Finance ticker symbol
]
```

### 3. Create a Gmail App Password
> You must use a **Google App Password** — not your regular Gmail password.

1. Go to [https://myaccount.google.com/security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** if not already on
3. Go to **App Passwords** → create one for "Mail"
4. Copy the 16-character password

### 4. Add GitHub Secrets
In your GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret Name         | Value                              |
|---------------------|------------------------------------|
| `GMAIL_SENDER`      | your Gmail address (e.g. you@gmail.com) |
| `GMAIL_APP_PASSWORD`| the 16-char App Password           |
| `EMAIL_RECIPIENT`   | where to send the report (can be same address) |

### 5. Push to GitHub
```bash
git add .
git commit -m "Initial setup"
git push origin main
```

GitHub Actions will automatically run **every Sunday at 8:00 AM UTC**.

---

## ▶️ Manual Test Run
Go to your repo → **Actions → Weekly Stock RSI Report → Run workflow**

This lets you test the email immediately without waiting for Sunday.

---

## ⏰ Change Schedule
Edit `.github/workflows/weekly_report.yml` and update the cron expression:
```yaml
- cron: "0 8 * * 0"   # Sunday 8AM UTC
- cron: "0 14 * * 5"  # Friday 2PM UTC
- cron: "0 9 * * 1"   # Monday 9AM UTC
```
Use [crontab.guru](https://crontab.guru) to build your own schedule.

---

## 📦 Dependencies
- `yfinance` — free Yahoo Finance data, no API key needed
- `pandas` — RSI calculation
- Standard library `smtplib` for Gmail SMTP

---

## ⚠️ Disclaimer
This tool is for informational purposes only. RSI is a technical indicator and does not constitute financial advice.
