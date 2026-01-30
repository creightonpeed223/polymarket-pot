/**
 * Polymarket Live Market Maker
 * Uses official CLOB client for real order placement
 */

const { ClobClient } = require("@polymarket/clob-client");
const { ethers } = require("ethers");
const axios = require("axios");

// Configuration
const CONFIG = {
    // Credentials
    PRIVATE_KEY: "0x0eb0bf286470345f20c9143a94cb81f7cc06e86e3695940092037023859da76a",
    API_KEY: "f007fe79-68a7-5567-9e8d-d9da37abf24e",
    API_SECRET: "Z6FjQyVmhkigPp1Fk8KyXDn2tFQQIrKGV76DsHgeKXg=",
    PASSPHRASE: "3fc1583d65e22039fef4af84f4a3e313037e7c4aec351ba0a466372aadd0b334",

    // Trading params - CONSERVATIVE for $3k account
    QUOTE_SIZE: 10,         // $10 per quote
    MAX_POSITION: 50,       // $50 max per market
    MIN_SPREAD: 0.02,       // 2% minimum spread
    MAX_SPREAD: 0.08,       // 8% max spread
    MAX_MARKETS: 10,        // Trade 10 markets
    QUOTE_REFRESH_MS: 5000, // Refresh every 5 seconds
    MIN_VOLUME: 50000,      // Only markets with $50k+ volume
};

// State
let client = null;
let wallet = null;
let markets = new Map();  // tokenId -> market state
let running = false;
let totalTrades = 0;
let totalVolume = 0;
let totalPnl = 0;

async function initialize() {
    console.log("=".repeat(60));
    console.log("POLYMARKET LIVE MARKET MAKER");
    console.log("=".repeat(60));

    // Create wallet
    wallet = new ethers.Wallet(CONFIG.PRIVATE_KEY);
    console.log(`Wallet: ${wallet.address}`);

    // Create CLOB client
    client = new ClobClient(
        "https://clob.polymarket.com",
        137,
        wallet,
        {
            key: CONFIG.API_KEY,
            secret: CONFIG.API_SECRET,
            passphrase: CONFIG.PASSPHRASE,
        }
    );

    console.log("CLOB client initialized");

    // Test connection
    try {
        const keys = await client.getApiKeys();
        console.log("✓ API authenticated");
    } catch (e) {
        console.error("✗ API auth failed:", e.message);
        return false;
    }

    // Load markets
    await loadMarkets();

    return true;
}

async function loadMarkets() {
    console.log("\nLoading markets from CLOB...");

    try {
        // Get markets directly from CLOB that are accepting orders
        const resp = await axios.get("https://clob.polymarket.com/sampling-markets");

        const data = resp.data.data || resp.data;
        console.log(`Found ${data.length} CLOB markets`);

        // Filter to markets accepting orders
        const eligible = data
            .filter(m => m.accepting_orders && m.enable_order_book && !m.closed)
            .slice(0, CONFIG.MAX_MARKETS);

        console.log(`Selected ${eligible.length} active markets:`);

        for (const m of eligible) {
            const tokens = m.tokens || [];
            if (tokens.length === 0) continue;

            const tokenId = tokens[0].token_id;
            const question = m.question || "Unknown";

            markets.set(tokenId, {
                tokenId,
                conditionId: m.condition_id,
                question: question.slice(0, 50),
                tickSize: m.minimum_tick_size || 0.01,
                minSize: m.minimum_order_size || 5,
                bestBid: 0,
                bestAsk: 1,
                position: 0,
                ourBidId: null,
                ourAskId: null,
                trades: 0,
                volume: 0,
                pnl: 0,
            });

            console.log(`  - ${question.slice(0, 45)}...`);
        }

        return true;
    } catch (e) {
        console.error("Error loading markets:", e.message);
        return false;
    }
}

async function updateOrderbook(state) {
    try {
        const book = await client.getOrderBook(state.tokenId);

        if (book.bids && book.bids.length > 0) {
            state.bestBid = parseFloat(book.bids[0].price);
        }
        if (book.asks && book.asks.length > 0) {
            state.bestAsk = parseFloat(book.asks[0].price);
        }
    } catch (e) {
        // Silently fail - will retry
    }
}

function calculateQuotes(state) {
    const spread = state.bestAsk - state.bestBid;
    const mid = (state.bestBid + state.bestAsk) / 2;

    // Skip if spread too tight or too wide
    if (spread < CONFIG.MIN_SPREAD || spread > CONFIG.MAX_SPREAD) {
        return { bidPrice: null, askPrice: null };
    }

    // Basic quote inside the spread
    let bidPrice = state.bestBid + 0.001;  // 0.1c inside best bid
    let askPrice = state.bestAsk - 0.001;  // 0.1c inside best ask

    // Inventory skew - adjust quotes based on position
    const skew = state.position * 0.001;  // 0.1% per $1 position
    bidPrice -= skew;
    askPrice -= skew;

    // Round to tick size (0.001)
    bidPrice = Math.round(bidPrice * 1000) / 1000;
    askPrice = Math.round(askPrice * 1000) / 1000;

    // Bounds
    bidPrice = Math.max(0.001, Math.min(0.99, bidPrice));
    askPrice = Math.max(0.01, Math.min(0.999, askPrice));

    // Ensure ask > bid
    if (askPrice <= bidPrice) {
        return { bidPrice: null, askPrice: null };
    }

    return { bidPrice, askPrice };
}

async function cancelOrder(orderId) {
    if (!orderId) return;
    try {
        await client.cancelOrder({ orderID: orderId });
    } catch (e) {
        // Order may already be filled/cancelled
    }
}

async function placeOrder(state, side, price, size) {
    try {
        // Use stored tick size
        const tickSize = state.tickSize || 0.01;
        const roundedPrice = Math.round(price / tickSize) * tickSize;

        // Ensure minimum size
        const orderSize = Math.max(size, state.minSize || 5);

        const orderArgs = {
            tokenID: state.tokenId,
            side: side,
            size: orderSize,
            price: roundedPrice,
        };

        const signedOrder = await client.createOrder(orderArgs);
        const result = await client.postOrder(signedOrder, "GTC");

        return result.orderID;
    } catch (e) {
        console.log(`  Order error (${state.question.slice(0, 20)}): ${e.message}`);
        return null;
    }
}

async function updateQuotes(state) {
    // Get current orderbook
    await updateOrderbook(state);

    // Calculate target quotes
    const { bidPrice, askPrice } = calculateQuotes(state);

    if (bidPrice === null || askPrice === null) {
        // Cancel existing quotes if spread not tradeable
        await cancelOrder(state.ourBidId);
        await cancelOrder(state.ourAskId);
        state.ourBidId = null;
        state.ourAskId = null;
        return;
    }

    // Update bid if needed
    if (state.position < CONFIG.MAX_POSITION) {
        await cancelOrder(state.ourBidId);
        state.ourBidId = await placeOrder(state, "BUY", bidPrice, CONFIG.QUOTE_SIZE);
        if (state.ourBidId) {
            console.log(`BID: ${state.question.slice(0, 25)}... @ $${bidPrice.toFixed(3)}`);
        }
    }

    // Update ask if needed
    if (state.position > -CONFIG.MAX_POSITION) {
        await cancelOrder(state.ourAskId);
        state.ourAskId = await placeOrder(state, "SELL", askPrice, CONFIG.QUOTE_SIZE);
        if (state.ourAskId) {
            console.log(`ASK: ${state.question.slice(0, 25)}... @ $${askPrice.toFixed(3)}`);
        }
    }
}

async function checkFills() {
    // Check for filled orders and update positions
    try {
        const trades = await client.getTrades();
        // Process any new fills and update position/pnl
        // This is simplified - in production you'd track order IDs
    } catch (e) {
        // Silently fail
    }
}

async function quoteLoop() {
    while (running) {
        for (const [tokenId, state] of markets) {
            if (!running) break;

            try {
                await updateQuotes(state);
            } catch (e) {
                console.log(`Quote error: ${e.message}`);
            }

            // Small delay between markets
            await sleep(200);
        }

        // Wait before next cycle
        await sleep(CONFIG.QUOTE_REFRESH_MS);
    }
}

async function cancelAllOrders() {
    console.log("\nCancelling all orders...");
    try {
        await client.cancelAll();
        console.log("✓ All orders cancelled");
    } catch (e) {
        console.log("Cancel all error:", e.message);
    }
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function printStats() {
    console.log("\n" + "-".repeat(40));
    console.log(`Markets: ${markets.size} | Trades: ${totalTrades} | Volume: $${totalVolume.toFixed(0)} | PnL: $${totalPnl.toFixed(2)}`);
    console.log("-".repeat(40));
}

async function run() {
    if (!await initialize()) {
        console.log("Initialization failed");
        return;
    }

    running = true;

    console.log("\n" + "=".repeat(60));
    console.log("MARKET MAKER RUNNING - LIVE TRADING");
    console.log(`Quote size: $${CONFIG.QUOTE_SIZE} | Max position: $${CONFIG.MAX_POSITION}`);
    console.log(`Spread: ${CONFIG.MIN_SPREAD * 100}%-${CONFIG.MAX_SPREAD * 100}% | Markets: ${markets.size}`);
    console.log("=".repeat(60));
    console.log("Press Ctrl+C to stop\n");

    // Handle shutdown
    process.on("SIGINT", async () => {
        console.log("\n\nShutting down...");
        running = false;
        await cancelAllOrders();
        printStats();
        process.exit(0);
    });

    // Start quoting
    await quoteLoop();
}

// Run
run().catch(console.error);
