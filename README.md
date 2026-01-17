# Polymarket Trading Bot

An AI-powered trading bot for Polymarket prediction markets. Includes both BTC price prediction and news arbitrage strategies.

## Strategy Documentation

- **[ARBITRAGE_STRATEGY.md](./ARBITRAGE_STRATEGY.md)** - Complete guide to news arbitrage ($10K/month blueprint)
- **[arbitrage_config.json](./arbitrage_config.json)** - Configuration for arbitrage bot

## Current Features

### 1. BTC Price Prediction (Experimental)
- 15-minute price direction prediction
- Technical indicators + ML model
- ~60% accuracy (educational/experimental)

## Features

- **Real-time BTC Price Data**: Live price streaming from Binance via ccxt
- **Technical Indicators**: RSI, MACD, Bollinger Bands, Stochastic, SMA/EMA
- **ML Predictions**: Gradient Boosting classifier for price direction
- **Multi-Timeframe Analysis**: Aggregates signals across 5m, 15m, 1h, 4h
- **Polymarket Integration**: Trade Bitcoin prediction markets
- **Risk Management**: Position limits, stop-loss, daily loss limits
- **Paper Trading**: Test strategies without risking real funds
- **Web Dashboard**: Real-time charts, signals, and trade controls

## Risk Warnings

1. This bot uses predictions that are NOT guaranteed to be accurate
2. Crypto markets are highly volatile - you can lose money
3. 15-minute predictions are especially unreliable
4. Always start with small amounts
5. Paper trade first before using real funds
6. The developer is not responsible for financial losses

## Requirements

- Python 3.11 or higher
- Node.js (optional, for development)

## Quick Start

### 1. Clone and Setup

```bash
cd polymarket-btc-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your settings (paper trading is enabled by default)
```

### 3. Run the Bot

```bash
# Start the server
python -m backend.main

# Or with uvicorn directly
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

### 4. Open Dashboard

Navigate to http://localhost:8000 in your browser.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POLYMARKET_PRIVATE_KEY` | - | Your Polymarket wallet private key |
| `POLYMARKET_FUNDER_ADDRESS` | - | Your Polymarket wallet address |
| `MAX_POSITION_SIZE` | 100 | Max position size in USD |
| `DAILY_LOSS_LIMIT` | 50 | Max daily loss in USD |
| `AUTO_TRADE_ENABLED` | false | Enable auto-trading on signals |
| `MIN_CONFIDENCE_THRESHOLD` | 70 | Min confidence to trade (0-100) |
| `PAPER_TRADING` | true | Paper trading mode |
| `PAPER_BALANCE` | 1000 | Paper trading starting balance |

See `.env.example` for all options.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/price` | GET | Current BTC price |
| `/api/price/history?tf=15m` | GET | Historical OHLCV data |
| `/api/indicators?tf=15m` | GET | Current indicator values |
| `/api/prediction` | GET | Current prediction & confidence |
| `/api/prediction/mtf` | GET | Multi-timeframe prediction |
| `/api/markets` | GET | Available BTC markets |
| `/api/positions` | GET | Open positions |
| `/api/trade` | POST | Execute a trade |
| `/api/settings` | GET/POST | Bot settings |
| `/ws/stream` | WebSocket | Real-time updates |

## Architecture

```
Backend (Python/FastAPI)
├── data/           # Price fetching & Polymarket client
├── analysis/       # Technical indicators & ML model
├── trading/        # Trade execution & risk management
└── utils/          # Logging utilities

Frontend (HTML/JS)
├── index.html      # Dashboard
├── css/            # Styles
└── js/             # App logic, charts, WebSocket
```

## Technical Indicators

- **Trend**: SMA (10, 30), EMA (12, 26), MACD
- **Momentum**: RSI (14), Stochastic (14, 3)
- **Volatility**: Bollinger Bands (20, 2), ATR (14)
- **Volume**: OBV with signal line

## ML Model

The ML prediction model uses a Gradient Boosting Classifier trained on:
- Price returns (1, 2, 5, 10 periods)
- Volatility features
- Technical indicator values
- Moving average ratios

Predictions are combined with technical signals using weighted averaging.

## Development

### Project Structure

```
polymarket-btc-bot/
├── backend/
│   ├── main.py              # FastAPI entry point
│   ├── config.py            # Configuration
│   ├── data/                # Data modules
│   ├── analysis/            # Analysis modules
│   ├── trading/             # Trading modules
│   └── utils/               # Utilities
├── frontend/
│   ├── index.html
│   ├── css/styles.css
│   └── js/
│       ├── app.js
│       ├── charts.js
│       └── websocket.js
├── .env.example
├── requirements.txt
└── README.md
```

### Running Tests

```bash
# Install dev dependencies
pip install pytest pytest-asyncio

# Run tests
pytest tests/
```

## License

MIT License - Use at your own risk.

## Disclaimer

This software is for educational purposes only. Trading cryptocurrency and prediction markets involves substantial risk of loss. Past performance does not guarantee future results. The developer assumes no responsibility for any financial losses incurred through the use of this software.
