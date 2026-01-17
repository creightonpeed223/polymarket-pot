/**
 * Main application logic for Polymarket BTC Trading Bot
 */

// State
let currentTimeframe = '15m';
let selectedMarketId = null;
let settings = {};

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    console.log('Initializing Polymarket BTC Trading Bot...');

    // Initialize chart
    chartManager = new ChartManager('chart-container');
    chartManager.initialize();
    await chartManager.loadData(currentTimeframe);

    // Connect WebSocket
    wsManager.connect();

    // Set up WebSocket handlers
    wsManager.on('price', handlePriceUpdate);
    wsManager.on('signal', handleSignalUpdate);

    // Load initial data
    await Promise.all([
        loadMarkets(),
        loadSettings(),
        loadIndicators(),
        loadPrediction(),
        loadPositions(),
        loadEvents(),
        loadMonitors(),
        loadTradeHistory(),
        loadTradeStatistics(),
    ]);

    // Set up auto-trade toggle
    document.getElementById('auto-trade-toggle').addEventListener('change', async (e) => {
        await updateSetting('auto_trade_enabled', e.target.checked);
    });

    // Set up trade history filter
    document.getElementById('history-filter').addEventListener('change', () => {
        loadTradeHistory();
    });

    // Periodic updates - prediction every 15s for responsive 15m trading
    setInterval(loadIndicators, 15000);
    setInterval(loadPrediction, 15000);
    setInterval(loadPositions, 10000);
    setInterval(loadEvents, 10000);  // Events every 10s
    setInterval(loadMonitors, 30000);  // Monitors every 30s
    setInterval(loadTradeHistory, 30000);  // Trade history every 30s
    setInterval(loadTradeStatistics, 30000);  // Trade stats every 30s
});

// Price update handler
function handlePriceUpdate(data) {
    const priceEl = document.getElementById('current-price');
    const changeEl = document.getElementById('price-change');

    if (data && data.price) {
        const price = parseFloat(data.price);
        priceEl.textContent = formatCurrency(price);
        priceEl.classList.add('price-pulse');
        setTimeout(() => priceEl.classList.remove('price-pulse'), 500);

        if (data.change_24h !== undefined) {
            const change = parseFloat(data.change_24h);
            changeEl.textContent = `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;
            changeEl.className = `text-lg ${change >= 0 ? 'text-green-500' : 'text-red-500'}`;
        }
    }
}

// Signal update handler
function handleSignalUpdate(data) {
    if (data) {
        updateSignalDisplay(data);
    }
}

// Load markets
async function loadMarkets() {
    try {
        const response = await fetch('/api/markets');
        const result = await response.json();

        const select = document.getElementById('market-select');
        select.innerHTML = '<option value="">Select a market...</option>';

        if (result.markets && result.markets.length > 0) {
            result.markets.forEach(market => {
                const option = document.createElement('option');
                option.value = market.id;
                option.textContent = truncateText(market.question, 60);
                select.appendChild(option);
            });

            // Select first market by default
            if (!selectedMarketId && result.markets.length > 0) {
                selectedMarketId = result.markets[0].id;
                select.value = selectedMarketId;
                await selectMarket(selectedMarketId);
            }
        } else {
            select.innerHTML = '<option value="">No BTC markets found</option>';
        }

        // Handle market selection
        select.addEventListener('change', async (e) => {
            if (e.target.value) {
                await selectMarket(e.target.value);
            }
        });
    } catch (error) {
        console.error('Failed to load markets:', error);
        showToast('Failed to load markets', 'error');
    }
}

// Select a market for trading
async function selectMarket(marketId) {
    try {
        const response = await fetch(`/api/markets/${marketId}/select`, {
            method: 'POST',
        });
        const result = await response.json();
        selectedMarketId = marketId;
        showToast('Market selected', 'success');
    } catch (error) {
        console.error('Failed to select market:', error);
        showToast('Failed to select market', 'error');
    }
}

// Load settings
async function loadSettings() {
    try {
        const response = await fetch('/api/settings');
        settings = await response.json();

        // Update UI
        document.getElementById('auto-trade-toggle').checked = settings.auto_trade_enabled;
        document.getElementById('setting-max-position').value = settings.max_position_size;
        document.getElementById('setting-daily-loss').value = settings.daily_loss_limit;
        document.getElementById('setting-min-confidence').value = settings.min_confidence_threshold;
        document.getElementById('setting-paper-trading').checked = settings.paper_trading;

        // Update paper mode badge
        const badge = document.getElementById('paper-mode-badge');
        if (settings.paper_trading) {
            badge.textContent = 'Paper Trading';
            badge.className = 'px-3 py-1 text-sm rounded-full bg-yellow-600';
        } else {
            badge.textContent = 'Live Trading';
            badge.className = 'px-3 py-1 text-sm rounded-full bg-red-600';
        }
    } catch (error) {
        console.error('Failed to load settings:', error);
    }
}

// Update a single setting
async function updateSetting(key, value) {
    try {
        const body = {};
        body[key] = value;

        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        if (response.ok) {
            settings[key] = value;
            showToast('Setting updated', 'success');
        }
    } catch (error) {
        console.error('Failed to update setting:', error);
        showToast('Failed to update setting', 'error');
    }
}

// Load indicators
async function loadIndicators() {
    try {
        const response = await fetch(`/api/indicators?tf=${currentTimeframe}`);
        const result = await response.json();

        if (result.indicators) {
            // RSI
            if (result.indicators.rsi !== undefined) {
                document.getElementById('indicator-rsi').textContent = result.indicators.rsi.toFixed(1);
            }

            // MACD
            if (result.indicators.macd !== undefined) {
                document.getElementById('indicator-macd').textContent = result.indicators.macd.toFixed(2);
            }

            // Bollinger %
            if (result.indicators.bb_percent !== undefined) {
                document.getElementById('indicator-bb').textContent = (result.indicators.bb_percent * 100).toFixed(1) + '%';
            }

            // Stochastic
            if (result.indicators.stoch_k !== undefined) {
                document.getElementById('indicator-stoch').textContent = result.indicators.stoch_k.toFixed(1);
            }
        }

        if (result.signals) {
            updateIndicatorSignals(result.signals);
        }
    } catch (error) {
        console.error('Failed to load indicators:', error);
    }
}

// Update indicator signal displays
function updateIndicatorSignals(signals) {
    const signalMap = {
        rsi: 'signal-rsi',
        macd: 'signal-macd',
        bollinger: 'signal-bb',
        stochastic: 'signal-stoch',
    };

    for (const [indicator, elementId] of Object.entries(signalMap)) {
        const el = document.getElementById(elementId);
        if (el && signals[indicator] !== undefined) {
            const value = signals[indicator];
            if (value > 0) {
                el.textContent = 'Bullish';
                el.className = 'text-sm indicator-bullish';
            } else if (value < 0) {
                el.textContent = 'Bearish';
                el.className = 'text-sm indicator-bearish';
            } else {
                el.textContent = 'Neutral';
                el.className = 'text-sm indicator-neutral';
            }
        }
    }
}

// Load prediction
async function loadPrediction() {
    try {
        // Use the dedicated 15m prediction endpoint
        const response = await fetch('/api/prediction/15m');
        const result = await response.json();
        update15mPredictionDisplay(result);
    } catch (error) {
        console.error('Failed to load prediction:', error);
    }
}

// Update the 15m prediction display
function update15mPredictionDisplay(data) {
    const directionEl = document.getElementById('signal-direction');
    const confidenceEl = document.getElementById('signal-confidence');
    const probUpBar = document.getElementById('prob-up-bar');
    const probDownBar = document.getElementById('prob-down-bar');
    const probUpText = document.getElementById('prob-up-text');
    const probDownText = document.getElementById('prob-down-text');
    const bullishCount = document.getElementById('bullish-count');
    const bearishCount = document.getElementById('bearish-count');
    const reasonsEl = document.getElementById('signal-reasons');

    if (!data.prediction) return;

    const direction = data.prediction.direction;
    const confidence = data.prediction.confidence;
    const probs = data.prediction.probabilities;

    // Direction
    directionEl.textContent = direction;
    if (direction === 'UP') {
        directionEl.className = 'text-5xl font-bold mb-2 text-green-400';
    } else if (direction === 'DOWN') {
        directionEl.className = 'text-5xl font-bold mb-2 text-red-400';
    } else {
        directionEl.className = 'text-5xl font-bold mb-2 text-gray-400';
    }

    // Confidence
    confidenceEl.textContent = `${confidence.toFixed(1)}%`;

    // Probability bars
    const upPct = (probs.UP * 100).toFixed(1);
    const downPct = (probs.DOWN * 100).toFixed(1);

    probUpBar.style.width = `${upPct}%`;
    probDownBar.style.width = `${downPct}%`;
    probUpText.textContent = `${upPct}%`;
    probDownText.textContent = `${downPct}%`;

    // Signal counts
    if (data.signals) {
        bullishCount.textContent = data.signals.bullish_count || 0;
        bearishCount.textContent = data.signals.bearish_count || 0;
    }

    // Reasons
    reasonsEl.innerHTML = '';
    if (data.model_ready) {
        const reason = document.createElement('div');
        reason.className = 'flex items-center space-x-2';
        reason.innerHTML = `<span class="w-2 h-2 rounded-full ${direction === 'UP' ? 'bg-green-500' : 'bg-red-500'}"></span>
            <span>ML predicts ${direction} with ${confidence.toFixed(0)}% confidence</span>`;
        reasonsEl.appendChild(reason);
    }

    // Update Polymarket sentiment
    updatePolymarketSentiment(data.polymarket);
}

// Update Polymarket sentiment display
function updatePolymarketSentiment(polymarket) {
    const sentimentEl = document.getElementById('polymarket-sentiment');
    const marketsEl = document.getElementById('polymarket-markets');

    if (!polymarket || !sentimentEl) return;

    const sentiment = polymarket.sentiment || 'NEUTRAL';
    sentimentEl.textContent = sentiment;

    // Color based on sentiment
    if (sentiment === 'BULLISH') {
        sentimentEl.className = 'text-sm font-bold px-2 py-1 rounded bg-green-600';
    } else if (sentiment === 'BEARISH') {
        sentimentEl.className = 'text-sm font-bold px-2 py-1 rounded bg-red-600';
    } else {
        sentimentEl.className = 'text-sm font-bold px-2 py-1 rounded bg-gray-600';
    }

    // Show market details
    if (marketsEl) {
        const count = polymarket.markets_count || 0;
        const ratio = polymarket.bullish_ratio || 0.5;
        marketsEl.innerHTML = `${count} market(s) analyzed | ${(ratio * 100).toFixed(0)}% bullish`;

        // Show top markets if available
        if (polymarket.top_markets && polymarket.top_markets.length > 0) {
            let html = `<div class="mt-2">`;
            polymarket.top_markets.forEach(m => {
                const yesPrice = (m.yes_price * 100).toFixed(0);
                html += `<div class="text-xs truncate" title="${m.question}">${m.question}: YES ${yesPrice}%</div>`;
            });
            html += `</div>`;
            marketsEl.innerHTML += html;
        }
    }
}

// Update signal display
function updateSignalDisplay(signal) {
    const directionEl = document.getElementById('signal-direction');
    const confidenceEl = document.getElementById('signal-confidence');
    const barEl = document.getElementById('confidence-bar');
    const reasonsEl = document.getElementById('signal-reasons');

    // Direction
    directionEl.textContent = signal.direction || 'HOLD';
    directionEl.className = `text-5xl font-bold mb-2 signal-${signal.direction?.toLowerCase() || 'hold'}`;

    // Confidence
    const confidence = signal.confidence || 50;
    confidenceEl.textContent = `${confidence.toFixed(0)}%`;

    // Confidence bar
    barEl.style.width = `${confidence}%`;
    if (confidence < 40) {
        barEl.className = 'h-3 rounded-full confidence-low transition-all';
    } else if (confidence < 70) {
        barEl.className = 'h-3 rounded-full confidence-medium transition-all';
    } else {
        barEl.className = 'h-3 rounded-full confidence-high transition-all';
    }

    // Reasons
    reasonsEl.innerHTML = '';
    if (signal.reasons && signal.reasons.length > 0) {
        signal.reasons.forEach(reason => {
            const div = document.createElement('div');
            div.className = 'flex items-center space-x-2';
            div.innerHTML = `<span class="w-2 h-2 rounded-full bg-orange-500"></span><span>${reason}</span>`;
            reasonsEl.appendChild(div);
        });
    }
}

// Load positions
async function loadPositions() {
    try {
        const response = await fetch('/api/positions');
        const result = await response.json();

        // Update portfolio summary
        if (result.summary) {
            document.getElementById('portfolio-cash').textContent = formatCurrency(result.summary.cash_balance);
            document.getElementById('portfolio-positions').textContent = formatCurrency(result.summary.total_value);

            // Total P&L
            const pnlEl = document.getElementById('portfolio-pnl');
            pnlEl.textContent = formatCurrency(result.summary.total_pnl, true);
            pnlEl.className = `font-semibold ${result.summary.total_pnl >= 0 ? 'text-green-500' : 'text-red-500'}`;

            // Realized P&L
            const realizedEl = document.getElementById('portfolio-realized-pnl');
            if (realizedEl) {
                realizedEl.textContent = formatCurrency(result.summary.realized_pnl || 0, true);
                realizedEl.className = `${(result.summary.realized_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`;
            }

            // Unrealized P&L
            const unrealizedEl = document.getElementById('portfolio-unrealized-pnl');
            if (unrealizedEl) {
                unrealizedEl.textContent = formatCurrency(result.summary.unrealized_pnl || 0, true);
                unrealizedEl.className = `${(result.summary.unrealized_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`;
            }

            // Daily P&L
            const dailyPnlEl = document.getElementById('portfolio-daily-pnl');
            if (dailyPnlEl) {
                dailyPnlEl.textContent = formatCurrency(result.summary.daily_pnl || 0, true);
                dailyPnlEl.className = `font-semibold ${(result.summary.daily_pnl || 0) >= 0 ? 'text-green-500' : 'text-red-500'}`;
            }

            // Trade stats in portfolio card
            const totalTradesEl = document.getElementById('stat-total-trades');
            if (totalTradesEl) totalTradesEl.textContent = result.summary.total_trades || 0;

            const winRateEl = document.getElementById('stat-win-rate');
            if (winRateEl) winRateEl.textContent = `${(result.summary.win_rate || 0).toFixed(1)}%`;
        }

        // Update positions list
        const listEl = document.getElementById('positions-list');
        listEl.innerHTML = '';

        if (result.positions && result.positions.length > 0) {
            result.positions.forEach(position => {
                const div = document.createElement('div');
                div.className = 'position-card bg-gray-700/50 rounded-lg p-3';
                div.innerHTML = `
                    <div class="flex justify-between items-start mb-2">
                        <span class="text-sm font-medium">${truncateText(position.market_name, 30)}</span>
                        <span class="text-xs px-2 py-1 rounded ${position.side === 'YES' ? 'bg-green-600' : 'bg-red-600'}">${position.side}</span>
                    </div>
                    <div class="flex justify-between text-sm mb-1">
                        <span class="text-gray-400">Entry: ${position.entry_price.toFixed(4)}</span>
                        <span class="text-gray-400">Current: ${position.current_price.toFixed(4)}</span>
                    </div>
                    <div class="flex justify-between text-sm mb-2">
                        <span class="text-gray-400">Risk: ${formatCurrency(position.risk_amount)}</span>
                        <span class="${position.pnl >= 0 ? 'text-green-400' : 'text-red-400'} font-semibold">${formatCurrency(position.pnl, true)} (${position.pnl_percent >= 0 ? '+' : ''}${position.pnl_percent.toFixed(1)}%)</span>
                    </div>
                    <button onclick="closePosition('${position.id}')" class="w-full py-1 text-sm bg-gray-600 hover:bg-gray-500 rounded transition">
                        Close Position
                    </button>
                `;
                listEl.appendChild(div);
            });
        } else {
            listEl.innerHTML = '<p class="text-gray-500 text-sm">No open positions</p>';
        }
    } catch (error) {
        console.error('Failed to load positions:', error);
    }
}

// Execute trade
async function executeTrade(side) {
    if (!selectedMarketId) {
        showToast('Please select a market first', 'error');
        return;
    }

    const size = parseFloat(document.getElementById('trade-size').value);
    if (isNaN(size) || size <= 0) {
        showToast('Please enter a valid trade size', 'error');
        return;
    }

    try {
        const response = await fetch('/api/trade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                market_id: selectedMarketId,
                side: side,
                size_usd: size,
            }),
        });

        const result = await response.json();

        if (response.ok) {
            showToast(`Trade executed: ${side} $${size}`, 'success');
            await loadPositions();
        } else {
            showToast(result.detail || 'Trade failed', 'error');
        }
    } catch (error) {
        console.error('Trade execution failed:', error);
        showToast('Trade execution failed', 'error');
    }
}

// Close position
async function closePosition(positionId) {
    try {
        const response = await fetch(`/api/positions/${positionId}/close`, {
            method: 'POST',
        });

        const result = await response.json();

        if (response.ok) {
            showToast('Position closed', 'success');
            await loadPositions();
        } else {
            showToast(result.detail || 'Failed to close position', 'error');
        }
    } catch (error) {
        console.error('Failed to close position:', error);
        showToast('Failed to close position', 'error');
    }
}

// Set timeframe
async function setTimeframe(tf) {
    currentTimeframe = tf;

    // Update button styles
    document.querySelectorAll('.tf-btn').forEach(btn => {
        btn.className = 'tf-btn px-3 py-1 rounded bg-gray-700 hover:bg-gray-600';
    });
    event.target.className = 'tf-btn px-3 py-1 rounded bg-orange-600';

    // Reload chart and indicators
    await chartManager.loadData(tf);
    await loadIndicators();
    await loadPrediction();
}

// Settings modal
function openSettings() {
    document.getElementById('settings-modal').classList.remove('hidden');
}

function closeSettings() {
    document.getElementById('settings-modal').classList.add('hidden');
}

async function saveSettings() {
    const newSettings = {
        max_position_size: parseFloat(document.getElementById('setting-max-position').value),
        daily_loss_limit: parseFloat(document.getElementById('setting-daily-loss').value),
        min_confidence_threshold: parseFloat(document.getElementById('setting-min-confidence').value),
        paper_trading: document.getElementById('setting-paper-trading').checked,
    };

    try {
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newSettings),
        });

        if (response.ok) {
            showToast('Settings saved', 'success');
            await loadSettings();
            closeSettings();
        } else {
            showToast('Failed to save settings', 'error');
        }
    } catch (error) {
        console.error('Failed to save settings:', error);
        showToast('Failed to save settings', 'error');
    }
}

// Utility functions
function formatCurrency(value, showSign = false) {
    const num = parseFloat(value) || 0;
    const formatted = Math.abs(num).toLocaleString('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });

    if (showSign) {
        return num >= 0 ? `+${formatted}` : `-${formatted.replace('$', '$')}`;
    }
    return formatted;
}

function truncateText(text, maxLength) {
    if (!text) return '';
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength - 3) + '...';
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type} px-4 py-3 rounded-lg shadow-lg text-white max-w-sm`;
    toast.textContent = message;

    container.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 4000);
}

// ============================================
// News Events Functions
// ============================================

// Load news events from autobot
async function loadEvents() {
    console.log('loadEvents() called');
    try {
        const [eventsResponse, matchesResponse, statusResponse] = await Promise.all([
            fetch('/api/events?limit=15'),
            fetch('/api/events/matches?limit=5'),
            fetch('/api/events/status'),
        ]);

        const eventsResult = await eventsResponse.json();
        const matchesResult = await matchesResponse.json();
        const statusResult = await statusResponse.json();

        console.log('Events loaded:', eventsResult.events?.length || 0);
        console.log('Status:', statusResult);

        // Update autobot status
        updateAutobotStatus(statusResult);

        // Update events list
        updateEventsList(eventsResult.events || []);

        // Update matches list
        updateMatchesList(matchesResult.matches || []);

    } catch (error) {
        console.error('Failed to load events:', error);
    }
}

// Load monitors status
async function loadMonitors() {
    try {
        const response = await fetch('/api/events/monitors');
        const result = await response.json();
        updateMonitorsList(result.monitors || []);
    } catch (error) {
        console.error('Failed to load monitors:', error);
    }
}

// Update autobot status display
function updateAutobotStatus(status) {
    const statusEl = document.getElementById('autobot-status');
    const countEl = document.getElementById('events-count');

    if (statusEl) {
        if (status.running) {
            statusEl.textContent = 'Online';
            statusEl.className = 'px-2 py-1 text-xs rounded-full bg-green-600';
        } else {
            statusEl.textContent = 'Offline';
            statusEl.className = 'px-2 py-1 text-xs rounded-full bg-gray-600';
        }
    }

    if (countEl) {
        countEl.textContent = `${status.events_processed || 0} events`;
    }
}

// Update monitors list display
function updateMonitorsList(monitors) {
    const listEl = document.getElementById('monitors-list');
    if (!listEl) return;

    if (monitors.length === 0) {
        listEl.innerHTML = '<span class="text-xs text-gray-500">No monitors active</span>';
        return;
    }

    listEl.innerHTML = monitors.map(monitor => {
        const statusColor = monitor.enabled ? 'bg-green-600' : 'bg-gray-600';
        const interval = monitor.check_interval ? `${monitor.check_interval}s` : '';
        return `
            <div class="flex items-center space-x-2 px-2 py-1 bg-gray-700 rounded text-xs">
                <span class="w-2 h-2 rounded-full ${statusColor}"></span>
                <span class="text-gray-300">${monitor.name}</span>
                ${interval ? `<span class="text-gray-500">(${interval})</span>` : ''}
            </div>
        `;
    }).join('');
}

// Update events list display
function updateEventsList(events) {
    console.log('updateEventsList called with', events.length, 'events');
    const listEl = document.getElementById('events-list');
    console.log('events-list element:', listEl);
    if (!listEl) {
        console.error('events-list element not found!');
        return;
    }

    if (events.length === 0) {
        listEl.innerHTML = '<p class="text-gray-500 text-sm">No events detected yet. Bot will monitor news sources for relevant events.</p>';
        return;
    }

    console.log('First event:', events[0]);

    listEl.innerHTML = events.map(event => {
        const eventTypeColors = {
            'court_ruling': 'bg-purple-600',
            'political_news': 'bg-blue-600',
            'regulatory_decision': 'bg-yellow-600',
            'legislation': 'bg-indigo-600',
            'fda_approval': 'bg-yellow-600',
            'sec_filing': 'bg-yellow-600',
            'twitter_announcement': 'bg-cyan-600',
            'sports_news': 'bg-green-600',
            'sports_injury': 'bg-orange-600',
            'sports_trade': 'bg-teal-600',
            'sports_result': 'bg-emerald-600',
        };
        const typeColor = eventTypeColors[event.event_type] || 'bg-gray-600';
        const timeAgo = formatTimeAgo(event.timestamp);
        const hasMatch = event.matched_market ? true : false;

        return `
            <div class="p-3 bg-gray-700/50 rounded-lg border-l-4 ${hasMatch ? 'border-green-500' : 'border-gray-600'}">
                <div class="flex items-start justify-between mb-1">
                    <span class="px-2 py-0.5 text-xs rounded ${typeColor}">${event.event_type || 'NEWS'}</span>
                    <span class="text-xs text-gray-500">${timeAgo}</span>
                </div>
                <p class="text-sm text-gray-200 mb-1">${truncateText(event.headline, 80)}</p>
                <div class="flex items-center justify-between text-xs">
                    <span class="text-gray-500">${event.source_name || 'Unknown'}</span>
                    ${event.confidence ? `<span class="text-gray-400">Confidence: ${(event.confidence * 100).toFixed(0)}%</span>` : ''}
                    ${event.edge ? `<span class="text-green-400">Edge: ${(event.edge * 100).toFixed(1)}%</span>` : ''}
                </div>
                ${event.matched_market ? `<div class="mt-1 text-xs text-green-400">Matched: ${truncateText(event.matched_market, 50)}</div>` : ''}
            </div>
        `;
    }).join('');
}

// Update matches list display
function updateMatchesList(matches) {
    const listEl = document.getElementById('matches-list');
    if (!listEl) return;

    if (matches.length === 0) {
        listEl.innerHTML = '<p class="text-gray-500 text-sm">No market matches yet.</p>';
        return;
    }

    listEl.innerHTML = matches.map(match => {
        const timeAgo = formatTimeAgo(match.timestamp);
        const edge = match.edge ? (match.edge * 100).toFixed(1) : '?';

        return `
            <div class="p-2 bg-gray-700/30 rounded">
                <div class="flex items-center justify-between mb-1">
                    <span class="text-xs text-gray-400">${timeAgo}</span>
                    <span class="text-xs px-2 py-0.5 rounded bg-green-600">${edge}% edge</span>
                </div>
                <p class="text-sm text-gray-300">${truncateText(match.market_question, 60)}</p>
                ${match.recommended_side ? `<span class="text-xs text-orange-400">Recommended: ${match.recommended_side}</span>` : ''}
            </div>
        `;
    }).join('');
}

// Format time ago helper
function formatTimeAgo(timestamp) {
    if (!timestamp) return 'Unknown';

    const now = new Date();
    const then = new Date(timestamp);
    const diffMs = now - then;
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);

    if (diffSec < 60) return `${diffSec}s ago`;
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    return then.toLocaleDateString();
}

// ============================================
// Trade History Functions
// ============================================

let allTrades = [];

// Load trade history
async function loadTradeHistory() {
    try {
        console.log('Loading trade history...');
        const response = await fetch('/api/trades/history?limit=100');

        if (!response.ok) {
            console.error('Trade history API error:', response.status, response.statusText);
            return;
        }

        const result = await response.json();
        console.log('Trade history loaded:', result.trades?.length || 0, 'trades');

        allTrades = result.trades || [];
        displayTradeHistory();

    } catch (error) {
        console.error('Failed to load trade history:', error);
    }
}

// Display trade history with filtering
function displayTradeHistory() {
    const filter = document.getElementById('history-filter').value;
    const tbody = document.getElementById('trade-history-body');
    const summaryEl = document.getElementById('history-summary');

    if (!tbody) return;

    // Apply filter
    let filteredTrades = allTrades;
    const today = new Date().toDateString();

    if (filter === 'today') {
        filteredTrades = allTrades.filter(t => new Date(t.closed_at).toDateString() === today);
    } else if (filter === 'winners') {
        filteredTrades = allTrades.filter(t => t.pnl > 0);
    } else if (filter === 'losers') {
        filteredTrades = allTrades.filter(t => t.pnl < 0);
    }

    // Update summary
    if (summaryEl) {
        summaryEl.textContent = `${filteredTrades.length} trade${filteredTrades.length !== 1 ? 's' : ''}`;
    }

    // Clear and populate table
    tbody.innerHTML = '';

    if (filteredTrades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="py-8 text-center text-gray-500">No closed trades yet</td></tr>';
        return;
    }

    filteredTrades.forEach(trade => {
        const row = document.createElement('tr');
        row.className = 'hover:bg-gray-700/30';

        const closeReasonColors = {
            'Take Profit': 'bg-green-600',
            'Stop Loss': 'bg-red-600',
            'Trailing Stop': 'bg-yellow-600',
            'Break Even': 'bg-gray-600',
            'Manual Close': 'bg-blue-600',
            'Market Closed': 'bg-purple-600',
        };

        const reasonColor = closeReasonColors[trade.close_reason] || 'bg-gray-600';
        const pnlColor = trade.pnl >= 0 ? 'text-green-400' : 'text-red-400';
        const closedTime = new Date(trade.closed_at);

        row.innerHTML = `
            <td class="py-3 px-2 text-gray-400">${closedTime.toLocaleString()}</td>
            <td class="py-3 px-2" title="${trade.market_name}">${truncateText(trade.market_name, 25)}</td>
            <td class="py-3 px-2 text-center">
                <span class="px-2 py-1 text-xs rounded ${trade.side === 'YES' ? 'bg-green-600' : 'bg-red-600'}">${trade.side}</span>
            </td>
            <td class="py-3 px-2 text-right text-gray-400">${formatCurrency(trade.risk_amount)}</td>
            <td class="py-3 px-2 text-right">${trade.entry_price.toFixed(4)}</td>
            <td class="py-3 px-2 text-right">${trade.exit_price.toFixed(4)}</td>
            <td class="py-3 px-2 text-right font-semibold ${pnlColor}">${formatCurrency(trade.pnl, true)}</td>
            <td class="py-3 px-2 text-right ${pnlColor}">${trade.pnl_percent >= 0 ? '+' : ''}${trade.pnl_percent.toFixed(2)}%</td>
            <td class="py-3 px-2 text-center">
                <span class="px-2 py-1 text-xs rounded ${reasonColor}">${trade.close_reason}</span>
            </td>
            <td class="py-3 px-2 text-right text-gray-400">${trade.duration_formatted}</td>
        `;

        tbody.appendChild(row);
    });
}

// Load trade statistics
async function loadTradeStatistics() {
    try {
        const response = await fetch('/api/trades/statistics');
        const stats = await response.json();

        // Update stats in portfolio card
        const profitFactorEl = document.getElementById('stat-profit-factor');
        if (profitFactorEl) {
            const pf = stats.profit_factor || 0;
            profitFactorEl.textContent = pf === Infinity ? 'INF' : pf.toFixed(2);
        }

        // Update stats in trade history section
        const avgWinEl = document.getElementById('stat-avg-win');
        if (avgWinEl) avgWinEl.textContent = formatCurrency(stats.average_win || 0);

        const avgLossEl = document.getElementById('stat-avg-loss');
        if (avgLossEl) avgLossEl.textContent = formatCurrency(stats.average_loss || 0);

        const largestWinEl = document.getElementById('stat-largest-win');
        if (largestWinEl) largestWinEl.textContent = formatCurrency(stats.largest_win || 0);

        const largestLossEl = document.getElementById('stat-largest-loss');
        if (largestLossEl) largestLossEl.textContent = formatCurrency(stats.largest_loss || 0);

        const totalRiskEl = document.getElementById('stat-total-risk');
        if (totalRiskEl) totalRiskEl.textContent = formatCurrency(stats.total_risk || 0);

        const avgDurationEl = document.getElementById('stat-avg-duration');
        if (avgDurationEl) {
            const seconds = stats.average_duration_seconds || 0;
            avgDurationEl.textContent = formatDuration(seconds);
        }

    } catch (error) {
        console.error('Failed to load trade statistics:', error);
    }
}

// Format duration helper
function formatDuration(seconds) {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    const hours = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${mins}m`;
}
