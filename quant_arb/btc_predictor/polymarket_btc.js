/**
 * Polymarket BTC Market Integration
 * Finds and trades 15-minute BTC prediction markets
 */

const { ClobClient } = require('@polymarket/clob-client');
const { ethers } = require('ethers');
const axios = require('axios');

class PolymarketBTC {
    constructor(config) {
        this.config = config;
        this.client = null;
        this.wallet = null;

        // Active BTC markets
        this.btcMarkets = new Map();  // marketId -> market info
        this.activeOrders = new Map(); // orderId -> order info

        // Stats
        this.stats = {
            tradesPlaced: 0,
            tradesWon: 0,
            tradesLost: 0,
            totalPnL: 0,
            openPositions: 0,
        };
    }

    /**
     * Generate slugs for current and upcoming BTC 15-minute windows
     */
    getCurrentBTCWindowSlugs() {
        const slugs = [];
        const now = Date.now();

        // Markets are created for 15-minute windows
        // Windows start at :00, :15, :30, :45 of each hour
        // Slug format: btc-updown-15m-{unix_timestamp_of_start}

        // Get current time and round down to nearest 15 minutes
        const msIn15Min = 15 * 60 * 1000;

        // Check current window and next few windows
        for (let offset = -1; offset <= 3; offset++) {
            // Round to nearest 15-minute boundary
            const windowStart = Math.floor(now / msIn15Min) * msIn15Min + (offset * msIn15Min);
            const timestamp = Math.floor(windowStart / 1000); // Convert to seconds

            slugs.push(`btc-updown-15m-${timestamp}`);
        }

        return slugs;
    }

    async connect() {
        console.log('Connecting to Polymarket...');

        this.wallet = new ethers.Wallet(this.config.privateKey);
        console.log('Wallet:', this.wallet.address);

        this.client = new ClobClient(
            'https://clob.polymarket.com',
            137,
            this.wallet,
            {
                key: this.config.apiKey,
                secret: this.config.apiSecret,
                passphrase: this.config.passphrase,
            }
        );

        // Verify connection
        try {
            const keys = await this.client.getApiKeys();
            console.log('✓ Polymarket authenticated');
            return true;
        } catch (e) {
            console.error('✗ Polymarket auth failed:', e.message);
            return false;
        }
    }

    /**
     * Find active BTC 15-minute Up/Down markets by constructing the slug
     */
    async findBTCMarkets() {
        console.log('Searching for BTC 15-minute Up/Down markets...');

        try {
            // Calculate current and upcoming 15-minute window timestamps
            const now = Date.now();
            const windowSlugs = this.getCurrentBTCWindowSlugs();

            const btcMarkets = [];

            // Try to fetch each potential market by slug
            for (const slug of windowSlugs) {
                try {
                    const resp = await axios.get('https://gamma-api.polymarket.com/events/slug/' + slug);
                    const event = resp.data;

                    if (event && event.markets && event.markets.length > 0) {
                        for (const m of event.markets) {
                            // Only include if market is active and accepting orders
                            if (m.active && !m.closed) {
                                btcMarkets.push({
                                    ...m,
                                    eventSlug: event.slug,
                                    eventTitle: event.title,
                                    seriesSlug: event.seriesSlug,
                                    eventStartTime: event.startTime,
                                });
                            }
                        }
                    }
                } catch (e) {
                    // Market not found or not active - this is expected
                }
            }

            console.log(`Found ${btcMarkets.length} BTC 15-minute Up/Down markets`);

            // Process each market
            for (const m of btcMarkets) {
                // Parse token IDs
                let tokens;
                try {
                    tokens = typeof m.clobTokenIds === 'string'
                        ? JSON.parse(m.clobTokenIds)
                        : m.clobTokenIds || [];
                } catch (e) {
                    continue;
                }

                if (tokens.length === 0) continue;

                // Parse outcomes
                let outcomes;
                try {
                    outcomes = typeof m.outcomes === 'string'
                        ? JSON.parse(m.outcomes)
                        : m.outcomes || [];
                } catch (e) {
                    outcomes = ['Up', 'Down'];
                }

                // Parse prices
                let prices;
                try {
                    prices = typeof m.outcomePrices === 'string'
                        ? JSON.parse(m.outcomePrices)
                        : m.outcomePrices || [];
                } catch (e) {
                    prices = [0.5, 0.5];
                }

                // Parse time window - use eventStartTime if available
                const startTime = m.eventStartTime ? new Date(m.eventStartTime).getTime() : null;
                const endTime = m.endDate ? new Date(m.endDate).getTime() : null;
                const timeInfo = {
                    startTime: startTime,
                    endTime: endTime,
                    durationMinutes: startTime && endTime ? (endTime - startTime) / 60000 : 15,
                };

                // Determine which token is Up and which is Down
                const upIndex = outcomes.findIndex(o => o.toLowerCase() === 'up');
                const downIndex = outcomes.findIndex(o => o.toLowerCase() === 'down');

                this.btcMarkets.set(m.conditionId, {
                    id: m.conditionId,
                    question: m.question,
                    slug: m.slug || m.eventSlug,
                    endDate: m.endDate,
                    tokens: tokens,
                    outcomes: outcomes,
                    prices: prices.map(p => parseFloat(p)),
                    upTokenId: upIndex >= 0 ? tokens[upIndex] : tokens[0],
                    downTokenId: downIndex >= 0 ? tokens[downIndex] : tokens[1],
                    upPrice: upIndex >= 0 ? parseFloat(prices[upIndex]) : parseFloat(prices[0]),
                    downPrice: downIndex >= 0 ? parseFloat(prices[downIndex]) : parseFloat(prices[1]),
                    bestBid: m.bestBid ? parseFloat(m.bestBid) : null,
                    bestAsk: m.bestAsk ? parseFloat(m.bestAsk) : null,
                    timeInfo: timeInfo,
                    volume: parseFloat(m.volume || m.volumeNum || 0),
                    liquidity: parseFloat(m.liquidity || m.liquidityNum || 0),
                    marketType: 'UP_DOWN',
                    acceptingOrders: m.acceptingOrders,
                });

                console.log(`  - ${m.question}`);
                console.log(`    Up: $${(upIndex >= 0 ? parseFloat(prices[upIndex]) : parseFloat(prices[0])).toFixed(3)} | Down: $${(downIndex >= 0 ? parseFloat(prices[downIndex]) : parseFloat(prices[1])).toFixed(3)}`);
                if (timeInfo.endTime) {
                    console.log(`    Ends: ${new Date(timeInfo.endTime).toLocaleTimeString()}`);
                }
            }

            return this.btcMarkets;

        } catch (e) {
            console.error('Error finding BTC markets:', e.message);
            return new Map();
        }
    }

    /**
     * Parse time window from market question or end date
     */
    parseTimeWindow(question, endDate) {
        const info = {
            startTime: null,
            endTime: null,
            durationMinutes: 15,
        };

        // Try to parse end date
        if (endDate) {
            info.endTime = new Date(endDate).getTime();
            info.startTime = info.endTime - 15 * 60 * 1000; // 15 minutes before
        }

        // Try to extract time from question (e.g., "2:15PM-2:30PM ET")
        const timeMatch = question.match(/(\d{1,2}):(\d{2})(AM|PM)\s*-\s*(\d{1,2}):(\d{2})(AM|PM)/i);
        if (timeMatch) {
            const now = new Date();
            let endHour = parseInt(timeMatch[4]);
            const endMin = parseInt(timeMatch[5]);
            const endAmPm = timeMatch[6].toUpperCase();

            if (endAmPm === 'PM' && endHour !== 12) endHour += 12;
            if (endAmPm === 'AM' && endHour === 12) endHour = 0;

            const endTime = new Date(now);
            endTime.setHours(endHour, endMin, 0, 0);

            // If time is in the past, it's tomorrow
            if (endTime < now) {
                endTime.setDate(endTime.getDate() + 1);
            }

            info.endTime = endTime.getTime();
            info.startTime = info.endTime - 15 * 60 * 1000;
        }

        return info;
    }

    /**
     * Get current market prices
     */
    async getMarketPrices(marketId) {
        const market = this.btcMarkets.get(marketId);
        if (!market) return null;

        try {
            // Get orderbook for YES token
            const book = await this.client.getOrderBook(market.tokens[0]);

            let yesPrice = 0.5;
            if (book.bids && book.bids.length > 0 && book.asks && book.asks.length > 0) {
                const bid = parseFloat(book.bids[0].price);
                const ask = parseFloat(book.asks[0].price);
                yesPrice = (bid + ask) / 2;
            }

            return {
                yes: yesPrice,
                no: 1 - yesPrice,
                bid: book.bids?.[0] ? parseFloat(book.bids[0].price) : null,
                ask: book.asks?.[0] ? parseFloat(book.asks[0].price) : null,
                spread: book.bids?.[0] && book.asks?.[0]
                    ? parseFloat(book.asks[0].price) - parseFloat(book.bids[0].price)
                    : null,
            };

        } catch (e) {
            return null;
        }
    }

    /**
     * Find best trading opportunity based on prediction for Up/Down markets
     */
    async findOpportunity(btcPrice, prediction) {
        const opportunities = [];

        for (const [marketId, market] of this.btcMarkets) {
            // Only trade Up/Down markets
            if (market.marketType !== 'UP_DOWN') continue;

            // Check if market is still active (not ended)
            const now = Date.now();
            if (market.timeInfo.endTime && market.timeInfo.endTime < now) {
                continue; // Market has ended
            }

            // Check if we're within the trading window (market started)
            if (market.timeInfo.startTime && market.timeInfo.startTime > now) {
                continue; // Market hasn't started yet
            }

            // Get live market prices
            const upPrices = await this.getUpDownPrices(market);
            if (!upPrices) continue;

            // Our probability estimate based on prediction
            // Convert our direction prediction to probability
            let ourUpProb = 0.5; // Base 50/50

            if (prediction.direction === 'UP') {
                // Scale probability based on confidence and expected move
                const moveSize = Math.abs(parseFloat(prediction.expectedMovePercent) || 0);
                const confidenceBoost = (prediction.confidence - 50) / 100; // 0 to 0.5

                // Higher move = higher probability
                const moveBoost = Math.min(0.3, moveSize * 5); // Up to 30% boost for moves

                ourUpProb = 0.5 + confidenceBoost * 0.3 + moveBoost;
                ourUpProb = Math.min(0.85, ourUpProb); // Cap at 85%
            } else if (prediction.direction === 'DOWN') {
                const moveSize = Math.abs(parseFloat(prediction.expectedMovePercent) || 0);
                const confidenceBoost = (prediction.confidence - 50) / 100;
                const moveBoost = Math.min(0.3, moveSize * 5);

                ourUpProb = 0.5 - confidenceBoost * 0.3 - moveBoost;
                ourUpProb = Math.max(0.15, ourUpProb); // Floor at 15%
            }

            // Calculate edge vs market
            const marketUpProb = upPrices.up;
            const marketDownProb = upPrices.down;

            const edgeUp = ourUpProb - marketUpProb;
            const edgeDown = (1 - ourUpProb) - marketDownProb;

            // Determine best side to trade
            let side, edge, ourProb, marketProb;
            if (edgeUp > edgeDown) {
                side = 'UP';
                edge = edgeUp;
                ourProb = ourUpProb;
                marketProb = marketUpProb;
            } else {
                side = 'DOWN';
                edge = edgeDown;
                ourProb = 1 - ourUpProb;
                marketProb = marketDownProb;
            }

            // Only trade if we have meaningful edge (3% minimum)
            if (edge > 0.03) {
                const timeToEnd = market.timeInfo.endTime ?
                    Math.round((market.timeInfo.endTime - now) / 60000) : null;

                opportunities.push({
                    marketId,
                    market,
                    side,
                    edge: edge * 100,
                    ourProb: ourProb * 100,
                    marketProb: marketProb * 100,
                    prices: upPrices,
                    timeToEnd: timeToEnd,
                    prediction: prediction.direction,
                    confidence: prediction.confidence,
                });
            }
        }

        // Sort by edge (highest first)
        opportunities.sort((a, b) => b.edge - a.edge);

        return opportunities;
    }

    /**
     * Get Up/Down prices for a market
     */
    async getUpDownPrices(market) {
        try {
            // Get orderbook for Up token
            const upBook = await this.client.getOrderBook(market.upTokenId);

            let upPrice = market.upPrice || 0.5;
            let upBid = null, upAsk = null;

            if (upBook.bids?.length > 0 && upBook.asks?.length > 0) {
                upBid = parseFloat(upBook.bids[0].price);
                upAsk = parseFloat(upBook.asks[0].price);
                upPrice = (upBid + upAsk) / 2;
            } else if (upBook.bids?.length > 0) {
                upBid = parseFloat(upBook.bids[0].price);
                upPrice = upBid;
            } else if (upBook.asks?.length > 0) {
                upAsk = parseFloat(upBook.asks[0].price);
                upPrice = upAsk;
            }

            return {
                up: upPrice,
                down: 1 - upPrice,
                upBid,
                upAsk,
                spread: upBid && upAsk ? upAsk - upBid : null,
            };
        } catch (e) {
            return null;
        }
    }

    /**
     * Place a trade on Up/Down market
     */
    async placeTrade(marketId, side, size) {
        const market = this.btcMarkets.get(marketId);
        if (!market) return null;

        // Get the correct token ID based on side
        const tokenId = side === 'UP' ? market.upTokenId : market.downTokenId;

        try {
            // Get current prices
            const prices = await this.getUpDownPrices(market);
            if (!prices) {
                console.log('No prices available');
                return null;
            }

            // Use ask price for buying
            const price = side === 'UP' ? (prices.upAsk || prices.up) : (1 - (prices.upBid || prices.up));

            if (!price || price <= 0 || price >= 1) {
                console.log('Invalid price:', price);
                return null;
            }

            console.log(`Placing ${side} order: $${size} @ ${price.toFixed(3)}`);

            const orderArgs = {
                tokenID: tokenId,
                side: 'BUY',
                size: size,
                price: price,
            };

            const signedOrder = await this.client.createOrder(orderArgs);
            const result = await this.client.postOrder(signedOrder, 'GTC');

            this.stats.tradesPlaced++;
            console.log(`✓ Order placed: ${result.orderID}`);

            return {
                orderId: result.orderID,
                marketId,
                side,
                price,
                size,
            };

        } catch (e) {
            console.error('Trade error:', e.message);
            return null;
        }
    }

    /**
     * Cancel all open orders
     */
    async cancelAllOrders() {
        try {
            await this.client.cancelAll();
            console.log('All orders cancelled');
        } catch (e) {
            console.error('Cancel error:', e.message);
        }
    }

    getStats() {
        return { ...this.stats };
    }
}

module.exports = { PolymarketBTC };
