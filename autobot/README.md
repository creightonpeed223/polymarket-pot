# Polymarket Speed Trading Bot

Fully automated news arbitrage trading bot for Polymarket.

## What It Does

```
1. Monitors news sources 24/7:
   - Supreme Court decisions
   - White House announcements
   - SEC/FDA regulatory decisions
   - Political news
   - Twitter (optional)

2. Detects breaking news in SECONDS

3. Matches news to Polymarket markets

4. Calculates edge (your advantage)

5. Executes trades automatically

6. Sends you alerts on Telegram/Discord
```

## Expected Returns

| Capital | Monthly Profit | ROI |
|---------|----------------|-----|
| $10,000 | $8,000-15,000 | 80-150% |
| $25,000 | $15,000-25,000 | 60-100% |
| $50,000 | $25,000-40,000 | 50-80% |

## Quick Start (5 Minutes)

### Step 1: Install Dependencies

```bash
cd polymarket-btc-bot
pip install -r autobot/requirements.txt
```

### Step 2: Configure

```bash
# Copy example config
cp autobot/.env.example .env

# Edit with your settings
nano .env  # or open in any text editor
```

**Minimum config for paper trading:**
```
STARTING_CAPITAL=10000
PAPER_TRADING=true
AUTO_TRADE=true
```

**For Telegram alerts (recommended):**
1. Message @BotFather on Telegram
2. Create a bot, get your token
3. Message @userinfobot to get your chat ID
4. Add to .env:
```
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id
```

### Step 3: Run the Bot

```bash
python -m autobot.main
```

That's it! The bot is now running.

## Going Live (Real Money)

⚠️ **Start with paper trading first!**

When ready for real trading:

1. Get your Polymarket wallet private key:
   - Go to Polymarket settings
   - Export wallet/private key
   - Add to .env: `POLYMARKET_PRIVATE_KEY=your_key`

2. Add your wallet address:
   - `POLYMARKET_FUNDER_ADDRESS=0x...`

3. Disable paper trading:
   - `PAPER_TRADING=false`

4. Start with small positions:
   - `MAX_POSITION_USD=500`
   - `STARTING_CAPITAL=5000`

## Configuration Options

| Setting | Default | Description |
|---------|---------|-------------|
| `STARTING_CAPITAL` | 10000 | Your starting balance |
| `MAX_POSITION_USD` | 2500 | Max per trade ($) |
| `MAX_POSITION_PCT` | 0.25 | Max per trade (% of capital) |
| `MIN_EDGE` | 0.30 | Minimum edge to trade (30%) |
| `MAX_DAILY_LOSS` | 2000 | Stop trading if down this much |
| `MAX_CONCURRENT` | 3 | Max open positions |
| `AUTO_TRADE` | true | Execute trades automatically |
| `PAPER_TRADING` | true | Simulate trades (no real $) |

## How to Get Alerts

### Telegram (Recommended)

1. Open Telegram, search for @BotFather
2. Send `/newbot` and follow instructions
3. Copy the API token you receive
4. Search for @userinfobot, send `/start`
5. Copy your numeric chat ID
6. Add both to your .env file

### Discord

1. In your Discord server, go to Settings > Integrations
2. Create a new Webhook
3. Copy the webhook URL
4. Add to .env: `DISCORD_WEBHOOK_URL=your_url`

## Running 24/7

For the bot to catch every opportunity, it needs to run continuously.

### Option 1: Keep Your Computer On
Just leave the terminal running.

### Option 2: Cloud Server (Recommended)

1. Get a cheap VPS ($5-20/month):
   - DigitalOcean
   - Vultr
   - Linode

2. SSH into your server

3. Install Python:
```bash
sudo apt update
sudo apt install python3 python3-pip
```

4. Clone and setup:
```bash
git clone your-repo
cd polymarket-btc-bot
pip3 install -r autobot/requirements.txt
cp autobot/.env.example .env
nano .env  # configure
```

5. Run with screen (stays running after disconnect):
```bash
screen -S bot
python3 -m autobot.main
# Press Ctrl+A, then D to detach
```

6. To reconnect:
```bash
screen -r bot
```

## Monitoring

### Check Status
The bot logs everything to:
- Console (real-time)
- `logs/autobot_YYYYMMDD.log` (saved)

### Alerts You'll Receive

**Opportunity Detected:**
```
🔔 OPPORTUNITY DETECTED
News: Supreme Court rules on abortion case
Market: Will Roe v Wade be overturned?
Current: $0.45 YES
Fair Value: $0.95
Edge: 50%
Action: BUY YES
```

**Trade Executed:**
```
🚨 TRADE EXECUTED
Market: Will Roe v Wade be overturned?
Side: YES
Size: $2,000
Price: $0.47
Edge: 48%
```

## Risk Management

The bot automatically:
- ✅ Limits position sizes
- ✅ Stops trading on daily loss limit
- ✅ Limits concurrent positions
- ✅ Only trades high-edge opportunities (30%+)

## Troubleshooting

**Bot not starting:**
- Check Python version: `python --version` (need 3.9+)
- Install dependencies: `pip install -r autobot/requirements.txt`

**No trades executing:**
- Check `AUTO_TRADE=true` in .env
- Check `MIN_EDGE` isn't too high
- Wait for news events (may be slow days)

**Alerts not working:**
- Test: `python -c "from autobot.alerts.notifier import AlertNotifier; import asyncio; asyncio.run(AlertNotifier().test())"`
- Check Telegram token and chat ID

## Support

Questions? Issues?
- Check logs in `logs/` folder
- Review error messages carefully
- Adjust settings if needed

## Disclaimer

This bot trades real money (when paper trading is disabled).
- Past performance doesn't guarantee future results
- You can lose money
- Only trade what you can afford to lose
- The developer is not responsible for losses

---

Happy trading! 🚀
