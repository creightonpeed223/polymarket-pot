const axios = require("axios");

async function findTradeable() {
    const resp = await axios.get("https://clob.polymarket.com/sampling-markets");
    const markets = resp.data.data;

    console.log("Checking", markets.length, "sampling markets...");

    for (const m of markets.slice(0, 30)) {
        if (!m.accepting_orders) continue;
        if (!m.tokens || m.tokens.length === 0) continue;

        const tokenId = m.tokens[0].token_id;

        try {
            const bookResp = await axios.get("https://clob.polymarket.com/book?token_id=" + tokenId);
            const book = bookResp.data;

            if (book.bids && book.asks && book.bids.length > 0 && book.asks.length > 0) {
                const bid = parseFloat(book.bids[0].price);
                const ask = parseFloat(book.asks[0].price);
                const spread = ask - bid;

                console.log();
                console.log("Market:", m.question.slice(0, 50));
                console.log("  Token:", tokenId.slice(0, 30) + "...");
                console.log("  Bid:", bid.toFixed(3), "(", book.bids.length, "levels)");
                console.log("  Ask:", ask.toFixed(3), "(", book.asks.length, "levels)");
                console.log("  Spread:", (spread * 100).toFixed(1) + "%");
            }
        } catch(e) {
            // No orderbook
        }
    }
}

findTradeable().catch(console.error);
