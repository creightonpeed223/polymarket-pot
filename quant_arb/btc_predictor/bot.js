/**
 * BTC 15-Minute Prediction Bot
 * Main entry point - combines data streaming, prediction, and trading
 */

const { BinanceStreamer } = require('./data_streamer');
const { BTCPredictor } = require('./predictor');
const { PolymarketBTC } = require('./polymarket_btc');
const http = require('http');

// Configuration
const CONFIG = {
    // Polymarket credentials
    privateKey: '0x0eb0bf286470345f20c9143a94cb81f7cc06e86e3695940092037023859da76a',
    apiKey: 'f007fe79-68a7-5567-9e8d-d9da37abf24e',
    apiSecret: 'Z6FjQyVmhkigPp1Fk8KyXDn2tFQQIrKGV76DsHgeKXg=',
    passphrase: '3fc1583d65e22039fef4af84f4a3e313037e7c4aec351ba0a466372aadd0b334',

    // Trading settings
    paperTrading: true,        // Set to false for live trading
    maxPositionSize: 50,       // Max $ per position
    minEdge: 5,                // Minimum edge % to trade
    maxDailyLoss: 100,         // Stop trading if down this much

    // Timing
    predictionInterval: 5000,  // Predict every 5 seconds
    dashboardPort: 8081,
};

// Global state
const streamer = new BinanceStreamer();
const predictor = new BTCPredictor();
let polymarket = null;

let running = false;
let latestPrediction = null;
let latestMetrics = null;
let opportunities = [];
let trades = [];

// Paper trading state
const paperState = {
    startingBalance: 1000,
    balance: 1000,
    positions: [],       // { marketId, side, size, entryPrice, entryTime, btcPriceAtEntry, marketEndTime }
    closedTrades: [],    // { ...position, exitPrice, pnl, result }
    dailyPnL: 0,
    totalPnL: 0,
    wins: 0,
    losses: 0,
};

/**
 * Initialize all components
 */
async function initialize() {
    console.log('='.repeat(60));
    console.log('BTC 15-MINUTE PREDICTION BOT');
    console.log('='.repeat(60));
    console.log(`Mode: ${CONFIG.paperTrading ? 'PAPER TRADING' : 'LIVE TRADING'}`);
    console.log('');

    // Connect to Binance
    await streamer.connect();

    // Setup Polymarket
    polymarket = new PolymarketBTC(CONFIG);
    const pmConnected = await polymarket.connect();

    if (pmConnected) {
        await polymarket.findBTCMarkets();
    }

    // Start dashboard
    startDashboard();

    return true;
}

/**
 * Main prediction loop
 */
async function predictionLoop() {
    console.log('\nStarting prediction loop...');
    console.log('Press Ctrl+C to stop\n');
    console.log(`Paper Trading Balance: $${paperState.balance.toFixed(2)}\n`);

    running = true;
    let lastMarketRefresh = 0;
    const MARKET_REFRESH_INTERVAL = 60000; // Refresh markets every minute

    while (running) {
        try {
            // Refresh markets periodically to find new 15-minute windows
            const now = Date.now();
            if (now - lastMarketRefresh > MARKET_REFRESH_INTERVAL) {
                if (polymarket) {
                    await polymarket.findBTCMarkets();
                    lastMarketRefresh = now;
                }
            }

            // Check for expired positions and resolve them
            await resolveExpiredPositions();

            // Get current metrics
            latestMetrics = streamer.getMetrics();

            if (latestMetrics.price > 0) {
                // Make prediction
                latestPrediction = predictor.predict(latestMetrics);

                if (latestPrediction) {
                    // Log significant predictions
                    if (latestPrediction.confidence >= 60 && latestPrediction.direction !== 'NEUTRAL') {
                        console.log(`[${new Date().toLocaleTimeString()}] BTC: $${latestMetrics.price.toFixed(0)} | ` +
                            `Prediction: ${latestPrediction.direction} ${latestPrediction.expectedMovePercent} | ` +
                            `Confidence: ${latestPrediction.confidence}%`);
                    }

                    // Find trading opportunities
                    if (polymarket && polymarket.btcMarkets.size > 0) {
                        opportunities = await polymarket.findOpportunity(latestMetrics.price, latestPrediction);

                        // Log opportunities found
                        if (opportunities.length > 0) {
                            const best = opportunities[0];
                            console.log(`[${new Date().toLocaleTimeString()}] 🎯 Opportunity: ${best.side} | ` +
                                `Edge: ${best.edge.toFixed(1)}% | ` +
                                `Our: ${best.ourProb.toFixed(0)}% vs Market: ${best.marketProb.toFixed(0)}%`);
                        }

                        // Paper trading - execute simulated trades
                        if (CONFIG.paperTrading && opportunities.length > 0 && paperState.dailyPnL > -CONFIG.maxDailyLoss) {
                            const best = opportunities[0];
                            if (best.edge >= CONFIG.minEdge) {
                                // Check if we already have a position in this market
                                const hasPosition = paperState.positions.some(p => p.marketId === best.marketId);
                                if (!hasPosition && paperState.balance >= 10) {
                                    executePaperTrade(best, latestMetrics.price);
                                }
                            }
                        }

                        // Live trading
                        if (!CONFIG.paperTrading && opportunities.length > 0 && paperState.dailyPnL > -CONFIG.maxDailyLoss) {
                            const best = opportunities[0];
                            if (best.edge >= CONFIG.minEdge) {
                                const size = Math.min(CONFIG.maxPositionSize, best.edge * 2);
                                const trade = await polymarket.placeTrade(best.marketId, best.side, size);

                                if (trade) {
                                    trades.push({
                                        ...trade,
                                        time: new Date().toLocaleTimeString(),
                                        edge: best.edge,
                                        btcPrice: latestMetrics.price,
                                        prediction: latestPrediction.direction,
                                    });
                                    console.log(`[${new Date().toLocaleTimeString()}] ✅ Trade executed: ${best.side} $${size.toFixed(2)}`);
                                }
                            }
                        }
                    }

                    // Record prediction for accuracy tracking
                    predictor.recordPrediction(latestPrediction);
                }
            }

        } catch (e) {
            console.error('Prediction loop error:', e.message);
        }

        await sleep(CONFIG.predictionInterval);
    }
}

/**
 * Execute a paper trade
 */
function executePaperTrade(opportunity, btcPrice) {
    const size = Math.min(CONFIG.maxPositionSize, Math.max(10, opportunity.edge * 2));
    const entryPrice = opportunity.side === 'UP' ? opportunity.prices.up : opportunity.prices.down;

    const position = {
        marketId: opportunity.marketId,
        marketQuestion: opportunity.market.question,
        side: opportunity.side,
        size: size,
        entryPrice: entryPrice,
        entryTime: Date.now(),
        btcPriceAtEntry: btcPrice,
        marketEndTime: opportunity.market.timeInfo.endTime,
        edge: opportunity.edge,
        prediction: opportunity.prediction,
    };

    paperState.positions.push(position);
    paperState.balance -= size;

    trades.push({
        time: new Date().toLocaleTimeString(),
        side: opportunity.side,
        size: size,
        price: entryPrice,
        edge: opportunity.edge,
        btcPrice: btcPrice,
        status: 'OPEN',
    });

    console.log(`[${new Date().toLocaleTimeString()}] 📝 PAPER TRADE: ${opportunity.side} $${size.toFixed(2)} @ ${entryPrice.toFixed(3)} | Edge: ${opportunity.edge.toFixed(1)}%`);
}

/**
 * Resolve expired paper positions
 */
async function resolveExpiredPositions() {
    const now = Date.now();
    const expiredPositions = paperState.positions.filter(p => p.marketEndTime && p.marketEndTime < now);

    for (const position of expiredPositions) {
        // Determine outcome based on BTC price movement
        // Up wins if price at end >= price at start (which we approximate with current price vs entry)
        const currentPrice = latestMetrics?.price || 0;
        const priceWentUp = currentPrice >= position.btcPriceAtEntry;

        const won = (position.side === 'UP' && priceWentUp) || (position.side === 'DOWN' && !priceWentUp);

        // Calculate PnL
        // If won: payout = size / entryPrice (you get $1 per share at resolution)
        // If lost: payout = 0
        const payout = won ? position.size / position.entryPrice : 0;
        const pnl = payout - position.size;

        // Update state
        paperState.balance += payout;
        paperState.totalPnL += pnl;
        paperState.dailyPnL += pnl;

        if (won) {
            paperState.wins++;
        } else {
            paperState.losses++;
        }

        // Record closed trade
        paperState.closedTrades.push({
            ...position,
            exitTime: now,
            btcPriceAtExit: currentPrice,
            won: won,
            payout: payout,
            pnl: pnl,
        });

        // Remove from open positions
        const idx = paperState.positions.indexOf(position);
        if (idx > -1) {
            paperState.positions.splice(idx, 1);
        }

        const resultEmoji = won ? '✅' : '❌';
        console.log(`[${new Date().toLocaleTimeString()}] ${resultEmoji} RESOLVED: ${position.side} | PnL: $${pnl.toFixed(2)} | BTC: $${position.btcPriceAtEntry.toFixed(0)} → $${currentPrice.toFixed(0)}`);
        console.log(`   Balance: $${paperState.balance.toFixed(2)} | Total PnL: $${paperState.totalPnL.toFixed(2)} | W/L: ${paperState.wins}/${paperState.losses}`);
    }
}

/**
 * Start web dashboard
 */
function startDashboard() {
    const server = http.createServer((req, res) => {
        res.setHeader('Content-Type', 'text/html');
        res.end(generateDashboard());
    });

    server.listen(CONFIG.dashboardPort, () => {
        console.log(`\n📊 Dashboard: http://localhost:${CONFIG.dashboardPort}\n`);
    });
}

/**
 * Generate dashboard HTML
 */
function generateDashboard() {
    const m = latestMetrics || {};
    const p = latestPrediction || {};
    const accuracy = predictor.getAccuracy();
    const stats = polymarket ? polymarket.getStats() : {};

    // Direction color
    const dirColor = p.direction === 'UP' ? '#4ade80' : p.direction === 'DOWN' ? '#f87171' : '#888';

    // PnL color
    const pnlColor = paperState.totalPnL >= 0 ? '#4ade80' : '#f87171';
    const dailyPnlColor = paperState.dailyPnL >= 0 ? '#4ade80' : '#f87171';

    // Open positions HTML
    let positionsHtml = '';
    for (const pos of paperState.positions) {
        const timeLeft = pos.marketEndTime ? Math.max(0, Math.round((pos.marketEndTime - Date.now()) / 60000)) : '?';
        positionsHtml += `
            <tr>
                <td style="color:${pos.side === 'UP' ? '#4ade80' : '#f87171'}">${pos.side}</td>
                <td>$${pos.size.toFixed(2)}</td>
                <td>${pos.entryPrice.toFixed(3)}</td>
                <td>$${pos.btcPriceAtEntry.toFixed(0)}</td>
                <td>${timeLeft}m</td>
            </tr>
        `;
    }
    if (!positionsHtml) positionsHtml = '<tr><td colspan="5" style="color:#666;text-align:center;">No open positions</td></tr>';

    // Closed trades HTML
    let closedHtml = '';
    for (const trade of paperState.closedTrades.slice(-10).reverse()) {
        const emoji = trade.won ? '✅' : '❌';
        closedHtml += `
            <tr>
                <td>${emoji} ${trade.side}</td>
                <td>$${trade.size.toFixed(2)}</td>
                <td style="color:${trade.pnl >= 0 ? '#4ade80' : '#f87171'}">$${trade.pnl.toFixed(2)}</td>
                <td>$${trade.btcPriceAtEntry.toFixed(0)} → $${trade.btcPriceAtExit?.toFixed(0) || '?'}</td>
            </tr>
        `;
    }
    if (!closedHtml) closedHtml = '<tr><td colspan="4" style="color:#666;text-align:center;">No closed trades yet</td></tr>';

    // Format bucket probabilities
    let bucketsHtml = '';
    if (p.bucketProbabilities) {
        const sorted = Object.entries(p.bucketProbabilities).sort((a, b) => parseInt(a[0]) - parseInt(b[0]));
        for (const [bucket, prob] of sorted) {
            const isSelected = parseInt(bucket) === p.predictedBucket;
            const barWidth = prob;
            bucketsHtml += `
                <div style="display:flex;align-items:center;margin:2px 0;">
                    <div style="width:80px;color:#888;">$${parseInt(bucket).toLocaleString()}</div>
                    <div style="flex:1;background:#333;height:20px;border-radius:3px;overflow:hidden;">
                        <div style="width:${barWidth}%;height:100%;background:${isSelected ? '#4ade80' : '#666'};"></div>
                    </div>
                    <div style="width:50px;text-align:right;${isSelected ? 'color:#4ade80;font-weight:bold;' : ''}">${prob}%</div>
                </div>
            `;
        }
    }

    // Active markets
    let marketsHtml = '';
    if (polymarket && polymarket.btcMarkets.size > 0) {
        for (const [id, market] of polymarket.btcMarkets) {
            const timeToEnd = market.timeInfo.endTime ?
                Math.max(0, Math.round((market.timeInfo.endTime - Date.now()) / 60000)) : '?';
            marketsHtml += `
                <tr>
                    <td style="max-width:250px;overflow:hidden;text-overflow:ellipsis;">${market.question}</td>
                    <td style="color:#4ade80">${(market.upPrice * 100).toFixed(1)}%</td>
                    <td style="color:#f87171">${(market.downPrice * 100).toFixed(1)}%</td>
                    <td>${timeToEnd}m</td>
                </tr>
            `;
        }
    }
    if (!marketsHtml) marketsHtml = '<tr><td colspan="4" style="color:#666;text-align:center;">No active markets</td></tr>';

    // Opportunities
    let oppsHtml = '';
    for (const opp of opportunities.slice(0, 5)) {
        oppsHtml += `
            <tr>
                <td style="color:${opp.side === 'UP' ? '#4ade80' : '#f87171'};font-weight:bold;">${opp.side}</td>
                <td>${opp.edge.toFixed(1)}%</td>
                <td>${opp.ourProb.toFixed(0)}%</td>
                <td>${opp.marketProb.toFixed(0)}%</td>
                <td>${opp.timeToEnd || '?'}m</td>
            </tr>
        `;
    }
    if (!oppsHtml) oppsHtml = '<tr><td colspan="5" style="color:#666;text-align:center;">No opportunities (need >3% edge)</td></tr>';

    // Recent trades
    let tradesHtml = '';
    for (const trade of trades.slice(-10).reverse()) {
        tradesHtml += `
            <tr>
                <td>${trade.time}</td>
                <td style="color:${trade.side === 'UP' ? '#4ade80' : '#f87171'}">${trade.side}</td>
                <td>$${trade.size}</td>
                <td>${trade.edge.toFixed(1)}%</td>
            </tr>
        `;
    }
    if (!tradesHtml) tradesHtml = '<tr><td colspan="4" style="color:#666;text-align:center;">No trades yet</td></tr>';

    return `
<!DOCTYPE html>
<html>
<head>
    <title>BTC Prediction Bot</title>
    <meta http-equiv="refresh" content="3">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Monaco', 'Menlo', monospace;
            background: #0a0a0a;
            color: #e0e0e0;
            padding: 20px;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 1px solid #333;
        }
        .title { font-size: 24px; font-weight: bold; }
        .mode {
            padding: 8px 16px;
            border-radius: 4px;
            background: ${CONFIG.paperTrading ? '#3d3d00' : '#1a472a'};
            color: ${CONFIG.paperTrading ? '#fbbf24' : '#4ade80'};
        }
        .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px; }
        .box {
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 15px;
        }
        .box-label { color: #888; font-size: 12px; margin-bottom: 5px; }
        .box-value { font-size: 24px; font-weight: bold; }
        .box-value.up { color: #4ade80; }
        .box-value.down { color: #f87171; }
        .section { margin-bottom: 20px; }
        .section-title {
            font-size: 14px;
            color: #888;
            margin-bottom: 10px;
            border-bottom: 1px solid #333;
            padding-bottom: 5px;
        }
        table { width: 100%; border-collapse: collapse; font-size: 12px; }
        th, td { padding: 8px; text-align: left; border-bottom: 1px solid #222; }
        th { color: #888; }
        .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .signals { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
        .signal {
            background: #1a1a1a;
            padding: 10px;
            border-radius: 5px;
            text-align: center;
        }
        .signal-name { font-size: 10px; color: #888; }
        .signal-value { font-size: 14px; font-weight: bold; }
    </style>
</head>
<body>
    <div class="header">
        <div class="title">🔮 BTC 15-MINUTE PREDICTION BOT</div>
        <div class="mode">${CONFIG.paperTrading ? 'PAPER' : 'LIVE'}</div>
    </div>

    <div class="grid" style="grid-template-columns: repeat(6, 1fr);">
        <div class="box">
            <div class="box-label">BALANCE</div>
            <div class="box-value">$${paperState.balance.toFixed(2)}</div>
        </div>
        <div class="box">
            <div class="box-label">TOTAL PnL</div>
            <div class="box-value" style="color:${pnlColor}">${paperState.totalPnL >= 0 ? '+' : ''}$${paperState.totalPnL.toFixed(2)}</div>
        </div>
        <div class="box">
            <div class="box-label">W / L</div>
            <div class="box-value">${paperState.wins} / ${paperState.losses}</div>
        </div>
        <div class="box">
            <div class="box-label">BTC PRICE</div>
            <div class="box-value">$${(m.price || 0).toLocaleString()}</div>
        </div>
        <div class="box">
            <div class="box-label">PREDICTION</div>
            <div class="box-value" style="color:${dirColor}">${p.direction || '-'} ${p.expectedMovePercent || ''}</div>
        </div>
        <div class="box">
            <div class="box-label">CONFIDENCE</div>
            <div class="box-value">${p.confidence || 0}%</div>
        </div>
    </div>

    <div class="two-col">
        <div class="section">
            <div class="section-title">PRICE BUCKET PROBABILITIES (15min)</div>
            <div class="box" style="padding:15px;">
                ${bucketsHtml || '<div style="color:#666;text-align:center;">Calculating...</div>'}
            </div>
        </div>

        <div class="section">
            <div class="section-title">MARKET SIGNALS</div>
            <div class="signals">
                <div class="signal">
                    <div class="signal-name">Momentum</div>
                    <div class="signal-value" style="color:${(m.momentum || 0) > 0 ? '#4ade80' : '#f87171'}">
                        ${((m.momentum || 0) / (m.price || 1) * 10000).toFixed(2)}
                    </div>
                </div>
                <div class="signal">
                    <div class="signal-name">RSI</div>
                    <div class="signal-value" style="color:${(m.rsi14 || 50) > 70 ? '#f87171' : (m.rsi14 || 50) < 30 ? '#4ade80' : '#888'}">
                        ${(m.rsi14 || 50).toFixed(1)}
                    </div>
                </div>
                <div class="signal">
                    <div class="signal-name">Order Flow</div>
                    <div class="signal-value" style="color:${(m.orderImbalance || 0) > 0 ? '#4ade80' : '#f87171'}">
                        ${((m.orderImbalance || 0) * 100).toFixed(1)}%
                    </div>
                </div>
                <div class="signal">
                    <div class="signal-name">1m Change</div>
                    <div class="signal-value" style="color:${(m.priceChange1m || 0) > 0 ? '#4ade80' : '#f87171'}">
                        ${((m.priceChange1m || 0) * 100).toFixed(3)}%
                    </div>
                </div>
                <div class="signal">
                    <div class="signal-name">5m Change</div>
                    <div class="signal-value" style="color:${(m.priceChange5m || 0) > 0 ? '#4ade80' : '#f87171'}">
                        ${((m.priceChange5m || 0) * 100).toFixed(3)}%
                    </div>
                </div>
                <div class="signal">
                    <div class="signal-name">Volatility</div>
                    <div class="signal-value">
                        ${((m.volatility5m || 0) * 100).toFixed(3)}%
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="section">
        <div class="section-title">ACTIVE 15-MINUTE BTC MARKETS</div>
        <table>
            <thead>
                <tr>
                    <th>Market</th>
                    <th>Up Price</th>
                    <th>Down Price</th>
                    <th>Time Left</th>
                </tr>
            </thead>
            <tbody>
                ${marketsHtml}
            </tbody>
        </table>
    </div>

    <div class="two-col">
        <div class="section">
            <div class="section-title">OPEN POSITIONS (${paperState.positions.length})</div>
            <table>
                <thead>
                    <tr>
                        <th>Side</th>
                        <th>Size</th>
                        <th>Entry</th>
                        <th>BTC Entry</th>
                        <th>Time Left</th>
                    </tr>
                </thead>
                <tbody>
                    ${positionsHtml}
                </tbody>
            </table>
        </div>

        <div class="section">
            <div class="section-title">TRADING OPPORTUNITIES</div>
            <table>
                <thead>
                    <tr>
                        <th>Side</th>
                        <th>Edge</th>
                        <th>Our Prob</th>
                        <th>Market</th>
                        <th>Time</th>
                    </tr>
                </thead>
                <tbody>
                    ${oppsHtml}
                </tbody>
            </table>
        </div>
    </div>

    <div class="section">
        <div class="section-title">CLOSED TRADES (${paperState.closedTrades.length})</div>
        <table>
            <thead>
                <tr>
                    <th>Result</th>
                    <th>Size</th>
                    <th>PnL</th>
                    <th>BTC Price</th>
                </tr>
            </thead>
            <tbody>
                ${closedHtml}
            </tbody>
        </table>
    </div>

    <div style="margin-top:20px;color:#444;font-size:11px;text-align:center;">
        Auto-refreshes every 3 seconds | Active Markets: ${polymarket?.btcMarkets?.size || 0} |
        Open Positions: ${paperState.positions.length} | Win Rate: ${paperState.wins + paperState.losses > 0 ? ((paperState.wins / (paperState.wins + paperState.losses)) * 100).toFixed(1) : 0}%
    </div>
</body>
</html>
    `;
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// Graceful shutdown
process.on('SIGINT', async () => {
    console.log('\n\nShutting down...');
    running = false;

    if (polymarket) {
        await polymarket.cancelAllOrders();
    }

    streamer.disconnect();

    console.log('\nFinal Stats:');
    console.log('  Predictions:', predictor.getAccuracy());
    console.log('  Trades:', polymarket?.getStats() || {});

    process.exit(0);
});

// Start
async function main() {
    await initialize();
    await predictionLoop();
}

main().catch(console.error);
