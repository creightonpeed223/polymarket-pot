const { ClobClient } = require("@polymarket/clob-client");
const { ethers } = require("ethers");

const PRIVATE_KEY = "0x0eb0bf286470345f20c9143a94cb81f7cc06e86e3695940092037023859da76a";
const API_KEY = "f007fe79-68a7-5567-9e8d-d9da37abf24e";
const API_SECRET = "Z6FjQyVmhkigPp1Fk8KyXDn2tFQQIrKGV76DsHgeKXg=";
const PASSPHRASE = "3fc1583d65e22039fef4af84f4a3e313037e7c4aec351ba0a466372aadd0b334";

// Positions to close
const POSITIONS = [
    {
        title: "San Antonio Spurs NBA Finals",
        tokenId: "102227184035967850089766981958743064457339118173548431660886438726896222843254",
        size: 852.42,
        currentPrice: 0.071,
    },
    {
        title: "Columbus Blue Jackets NHL",
        tokenId: "6772099324721707224715070437205709623671146843087680984461675764599992554029",
        size: 84.97,
        currentPrice: 0.007,
    },
    {
        title: "Ottawa Senators NHL",
        tokenId: "9887517307253276339498258777598347382044823812035708831949239151116896928806",
        size: 5.01,
        currentPrice: 0.008,
    },
];

async function main() {
    const wallet = new ethers.Wallet(PRIVATE_KEY);
    console.log("Wallet:", wallet.address);
    console.log("=" .repeat(60));

    const client = new ClobClient(
        "https://clob.polymarket.com",
        137,
        wallet,
        { key: API_KEY, secret: API_SECRET, passphrase: PASSPHRASE }
    );

    // First cancel any existing open orders
    console.log("\nCancelling any existing open orders...");
    try {
        const openOrders = await client.getOpenOrders();
        if (openOrders && openOrders.length > 0) {
            console.log(`Found ${openOrders.length} open orders`);
            for (const order of openOrders) {
                console.log(`  Cancelling order ${order.id.slice(0, 20)}...`);
                await client.cancelOrder({ orderID: order.id });
            }
            console.log("✓ All orders cancelled");
        } else {
            console.log("No open orders to cancel");
        }
    } catch (e) {
        console.log("Cancel error:", e.message);
    }

    // Now sell each position
    console.log("\n" + "=".repeat(60));
    console.log("CLOSING POSITIONS");
    console.log("=".repeat(60));

    for (const pos of POSITIONS) {
        console.log(`\nSelling: ${pos.title}`);
        console.log(`  Size: ${pos.size} shares`);
        console.log(`  Current: $${pos.currentPrice}`);

        // Sell at 50% of current price to ensure fill
        const sellPrice = Math.max(0.001, pos.currentPrice * 0.5);
        console.log(`  Sell price: $${sellPrice.toFixed(4)} (50% discount for quick fill)`);

        try {
            // Get tick size for this market
            const tickSize = await client.getTickSize(pos.tokenId);
            console.log(`  Tick size: ${tickSize}`);

            // Round price to tick size
            const roundedPrice = Math.round(sellPrice / parseFloat(tickSize)) * parseFloat(tickSize);
            const finalPrice = Math.max(parseFloat(tickSize), roundedPrice);

            // Create the order
            const orderArgs = {
                tokenID: pos.tokenId,
                side: "SELL",
                size: pos.size,
                price: finalPrice,
            };

            console.log(`  Creating order: SELL ${pos.size} @ $${finalPrice.toFixed(4)}`);
            const signedOrder = await client.createOrder(orderArgs);

            // Post the order
            const result = await client.postOrder(signedOrder, "GTC");
            console.log(`  ✓ Order placed: ${result.orderID || JSON.stringify(result)}`);

        } catch (e) {
            console.log(`  ✗ Error: ${e.message}`);
            if (e.response && e.response.data) {
                console.log(`    Details: ${JSON.stringify(e.response.data)}`);
            }
        }
    }

    console.log("\n" + "=".repeat(60));
    console.log("Done! Check Polymarket to see your sell orders.");
    console.log("Orders are set at 50% below market - they should fill quickly.");
    console.log("=".repeat(60));
}

main().catch(console.error);
