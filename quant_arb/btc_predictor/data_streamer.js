/**
 * Real-time BTC Data Streamer
 * Streams price, orderbook, and trades from Binance
 */

const WebSocket = require('ws');
const EventEmitter = require('events');

class BinanceStreamer extends EventEmitter {
    constructor() {
        super();
        this.ws = null;
        this.wsBook = null;
        this.connected = false;

        // Current state
        this.price = 0;
        this.bid = 0;
        this.ask = 0;
        this.volume24h = 0;

        // Price history for calculations
        this.priceHistory = [];      // {time, price, volume}
        this.tradeHistory = [];      // Recent trades
        this.bookHistory = [];       // Orderbook snapshots

        // Orderbook state
        this.bids = new Map();       // price -> size
        this.asks = new Map();       // price -> size

        // Aggregated metrics
        this.metrics = {
            price: 0,
            priceChange1m: 0,
            priceChange5m: 0,
            priceChange15m: 0,
            momentum: 0,
            volatility1m: 0,
            volatility5m: 0,
            vwap: 0,
            rsi14: 50,
            buyVolume1m: 0,
            sellVolume1m: 0,
            orderImbalance: 0,
            bidDepth: 0,
            askDepth: 0,
            spread: 0,
            lastUpdate: 0,
        };

        this.maxHistoryLength = 1000; // ~16 minutes at 1/sec
    }

    async connect() {
        return new Promise((resolve) => {
            console.log('Connecting to Binance WebSocket...');

            // Combined stream: trades + klines + ticker
            const streams = [
                'btcusdt@trade',
                'btcusdt@kline_1m',
                'btcusdt@ticker',
                'btcusdt@depth20@100ms'
            ].join('/');

            this.ws = new WebSocket(`wss://stream.binance.com:9443/stream?streams=${streams}`);

            this.ws.on('open', () => {
                console.log('✓ Binance WebSocket connected');
                this.connected = true;
                resolve(true);
            });

            this.ws.on('message', (data) => {
                this.handleMessage(JSON.parse(data));
            });

            this.ws.on('close', () => {
                console.log('Binance WebSocket disconnected');
                this.connected = false;
                setTimeout(() => this.connect(), 5000);
            });

            this.ws.on('error', (err) => {
                console.error('WebSocket error:', err.message);
            });
        });
    }

    handleMessage(msg) {
        const stream = msg.stream;
        const data = msg.data;

        if (stream.includes('@trade')) {
            this.handleTrade(data);
        } else if (stream.includes('@kline')) {
            this.handleKline(data);
        } else if (stream.includes('@ticker')) {
            this.handleTicker(data);
        } else if (stream.includes('@depth')) {
            this.handleDepth(data);
        }

        // Emit update event
        this.emit('update', this.metrics);
    }

    handleTrade(trade) {
        const price = parseFloat(trade.p);
        const qty = parseFloat(trade.q);
        const isBuyerMaker = trade.m; // true = sell, false = buy
        const time = trade.T;

        this.price = price;
        this.metrics.price = price;
        this.metrics.lastUpdate = Date.now();

        // Add to trade history
        this.tradeHistory.push({
            time,
            price,
            qty,
            side: isBuyerMaker ? 'sell' : 'buy',
        });

        // Keep only last 1000 trades
        if (this.tradeHistory.length > 1000) {
            this.tradeHistory.shift();
        }

        // Calculate buy/sell volume in last minute
        const oneMinAgo = Date.now() - 60000;
        const recentTrades = this.tradeHistory.filter(t => t.time > oneMinAgo);

        this.metrics.buyVolume1m = recentTrades
            .filter(t => t.side === 'buy')
            .reduce((sum, t) => sum + t.qty * t.price, 0);

        this.metrics.sellVolume1m = recentTrades
            .filter(t => t.side === 'sell')
            .reduce((sum, t) => sum + t.qty * t.price, 0);

        // Order flow imbalance
        const totalVol = this.metrics.buyVolume1m + this.metrics.sellVolume1m;
        if (totalVol > 0) {
            this.metrics.orderImbalance = (this.metrics.buyVolume1m - this.metrics.sellVolume1m) / totalVol;
        }

        // Add to price history (sample every second)
        const lastEntry = this.priceHistory[this.priceHistory.length - 1];
        if (!lastEntry || time - lastEntry.time >= 1000) {
            this.priceHistory.push({ time, price, volume: qty * price });

            if (this.priceHistory.length > this.maxHistoryLength) {
                this.priceHistory.shift();
            }

            // Recalculate metrics
            this.calculateMetrics();
        }

        this.emit('trade', { price, qty, side: isBuyerMaker ? 'sell' : 'buy' });
    }

    handleKline(kline) {
        const k = kline.k;
        // Can use for additional OHLC data if needed
    }

    handleTicker(ticker) {
        this.volume24h = parseFloat(ticker.q); // Quote volume
        this.metrics.priceChange24h = parseFloat(ticker.P); // 24h change %
    }

    handleDepth(depth) {
        // Update orderbook
        this.bids.clear();
        this.asks.clear();

        for (const [price, size] of depth.bids) {
            this.bids.set(parseFloat(price), parseFloat(size));
        }
        for (const [price, size] of depth.asks) {
            this.asks.set(parseFloat(price), parseFloat(size));
        }

        // Calculate book metrics
        const bidPrices = Array.from(this.bids.keys()).sort((a, b) => b - a);
        const askPrices = Array.from(this.asks.keys()).sort((a, b) => a - b);

        if (bidPrices.length > 0 && askPrices.length > 0) {
            this.bid = bidPrices[0];
            this.ask = askPrices[0];
            this.metrics.spread = (this.ask - this.bid) / this.bid;

            // Calculate depth within 0.1% of mid
            const mid = (this.bid + this.ask) / 2;
            const range = mid * 0.001; // 0.1%

            this.metrics.bidDepth = bidPrices
                .filter(p => p >= mid - range)
                .reduce((sum, p) => sum + this.bids.get(p) * p, 0);

            this.metrics.askDepth = askPrices
                .filter(p => p <= mid + range)
                .reduce((sum, p) => sum + this.asks.get(p) * p, 0);
        }
    }

    calculateMetrics() {
        const now = Date.now();
        const prices = this.priceHistory;

        if (prices.length < 2) return;

        const currentPrice = prices[prices.length - 1].price;

        // Price changes
        const oneMinAgo = prices.find(p => p.time <= now - 60000);
        const fiveMinAgo = prices.find(p => p.time <= now - 300000);
        const fifteenMinAgo = prices.find(p => p.time <= now - 900000);

        if (oneMinAgo) {
            this.metrics.priceChange1m = (currentPrice - oneMinAgo.price) / oneMinAgo.price;
        }
        if (fiveMinAgo) {
            this.metrics.priceChange5m = (currentPrice - fiveMinAgo.price) / fiveMinAgo.price;
        }
        if (fifteenMinAgo) {
            this.metrics.priceChange15m = (currentPrice - fifteenMinAgo.price) / fifteenMinAgo.price;
        }

        // Momentum (rate of change)
        if (prices.length >= 10) {
            const recent = prices.slice(-10);
            const changes = [];
            for (let i = 1; i < recent.length; i++) {
                changes.push(recent[i].price - recent[i - 1].price);
            }
            this.metrics.momentum = changes.reduce((a, b) => a + b, 0) / changes.length;
        }

        // Volatility (std dev of returns)
        if (prices.length >= 60) {
            const recent = prices.slice(-60); // Last minute
            const returns = [];
            for (let i = 1; i < recent.length; i++) {
                returns.push((recent[i].price - recent[i - 1].price) / recent[i - 1].price);
            }
            const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
            const variance = returns.reduce((sum, r) => sum + Math.pow(r - mean, 2), 0) / returns.length;
            this.metrics.volatility1m = Math.sqrt(variance) * Math.sqrt(60); // Annualized per minute
        }

        if (prices.length >= 300) {
            const recent = prices.slice(-300); // Last 5 minutes
            const returns = [];
            for (let i = 1; i < recent.length; i++) {
                returns.push((recent[i].price - recent[i - 1].price) / recent[i - 1].price);
            }
            const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
            const variance = returns.reduce((sum, r) => sum + Math.pow(r - mean, 2), 0) / returns.length;
            this.metrics.volatility5m = Math.sqrt(variance) * Math.sqrt(300);
        }

        // VWAP
        const vwapPeriod = prices.slice(-60);
        const totalVolume = vwapPeriod.reduce((sum, p) => sum + (p.volume || 0), 0);
        if (totalVolume > 0) {
            this.metrics.vwap = vwapPeriod.reduce((sum, p) => sum + p.price * (p.volume || 0), 0) / totalVolume;
        }

        // RSI (14 periods, using 1-second samples)
        if (prices.length >= 15) {
            const recent = prices.slice(-15);
            let gains = 0, losses = 0;
            for (let i = 1; i < recent.length; i++) {
                const change = recent[i].price - recent[i - 1].price;
                if (change > 0) gains += change;
                else losses -= change;
            }
            const avgGain = gains / 14;
            const avgLoss = losses / 14;
            if (avgLoss === 0) {
                this.metrics.rsi14 = 100;
            } else {
                const rs = avgGain / avgLoss;
                this.metrics.rsi14 = 100 - (100 / (1 + rs));
            }
        }
    }

    getMetrics() {
        return { ...this.metrics };
    }

    getPriceHistory(minutes = 15) {
        const cutoff = Date.now() - minutes * 60 * 1000;
        return this.priceHistory.filter(p => p.time >= cutoff);
    }

    disconnect() {
        if (this.ws) {
            this.ws.close();
        }
    }
}

module.exports = { BinanceStreamer };
