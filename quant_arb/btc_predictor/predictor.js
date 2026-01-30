/**
 * BTC 15-Minute Price Predictor
 * Uses multiple signals to predict where BTC will be in 15 minutes
 */

class BTCPredictor {
    constructor() {
        // Prediction weights (tunable)
        this.weights = {
            momentum: 0.25,
            orderFlow: 0.20,
            rsi: 0.15,
            volatility: 0.15,
            vwap: 0.15,
            trend: 0.10,
        };

        // Historical predictions for accuracy tracking
        this.predictions = [];
        this.accuracy = {
            total: 0,
            correct: 0,
            avgError: 0,
        };
    }

    /**
     * Predict BTC price in 15 minutes
     * @param {object} metrics - Current market metrics from data streamer
     * @returns {object} - Prediction with confidence and reasoning
     */
    predict(metrics) {
        const currentPrice = metrics.price;
        if (!currentPrice || currentPrice === 0) {
            return null;
        }

        // Calculate individual signals (each returns expected % move)
        const signals = {
            momentum: this.momentumSignal(metrics),
            orderFlow: this.orderFlowSignal(metrics),
            rsi: this.rsiSignal(metrics),
            volatility: this.volatilitySignal(metrics),
            vwap: this.vwapSignal(metrics),
            trend: this.trendSignal(metrics),
        };

        // Weighted average of signals
        let weightedMove = 0;
        let totalWeight = 0;

        for (const [signal, value] of Object.entries(signals)) {
            if (value !== null && !isNaN(value)) {
                weightedMove += value * this.weights[signal];
                totalWeight += this.weights[signal];
            }
        }

        if (totalWeight > 0) {
            weightedMove /= totalWeight;
        }

        // Predicted price
        const predictedPrice = currentPrice * (1 + weightedMove);

        // Confidence based on signal agreement
        const signalValues = Object.values(signals).filter(v => v !== null && !isNaN(v));
        const signalAgreement = this.calculateAgreement(signalValues);

        // Confidence factors
        const volatilityConfidence = Math.max(0, 1 - metrics.volatility5m * 10); // Lower confidence in high vol
        const dataConfidence = Math.min(1, metrics.lastUpdate ? 1 : 0);

        const confidence = signalAgreement * 0.5 + volatilityConfidence * 0.3 + dataConfidence * 0.2;

        // Price buckets for Polymarket (typical $500 buckets)
        const bucketSize = 500;
        const currentBucket = Math.floor(currentPrice / bucketSize) * bucketSize;
        const predictedBucket = Math.floor(predictedPrice / bucketSize) * bucketSize;

        // Probabilities for each bucket
        const bucketProbabilities = this.calculateBucketProbabilities(
            currentPrice,
            predictedPrice,
            metrics.volatility5m,
            bucketSize
        );

        return {
            timestamp: Date.now(),
            currentPrice,
            predictedPrice,
            expectedMove: weightedMove,
            expectedMovePercent: (weightedMove * 100).toFixed(3) + '%',
            confidence: Math.round(confidence * 100),
            signals,
            direction: weightedMove > 0.0005 ? 'UP' : weightedMove < -0.0005 ? 'DOWN' : 'NEUTRAL',
            currentBucket,
            predictedBucket,
            bucketProbabilities,
            recommendation: this.getRecommendation(weightedMove, confidence, bucketProbabilities),
        };
    }

    /**
     * Momentum signal - recent price momentum predicts continuation
     */
    momentumSignal(metrics) {
        const momentum = metrics.momentum || 0;
        const priceChange1m = metrics.priceChange1m || 0;
        const priceChange5m = metrics.priceChange5m || 0;

        // Weight recent momentum more heavily
        const signal = (priceChange1m * 0.5 + priceChange5m * 0.3 + (momentum / metrics.price) * 0.2);

        // Dampen extreme moves (mean reversion tendency)
        const dampened = signal * 0.7;

        // Project forward 15 minutes (3x the 5-minute move, dampened)
        return dampened * 2;
    }

    /**
     * Order flow signal - buy/sell imbalance predicts direction
     */
    orderFlowSignal(metrics) {
        const imbalance = metrics.orderImbalance || 0;

        // Strong imbalance = predicted move in that direction
        // Typical imbalance ranges from -0.5 to +0.5
        return imbalance * 0.005; // Max 0.25% move from order flow
    }

    /**
     * RSI signal - overbought/oversold conditions
     */
    rsiSignal(metrics) {
        const rsi = metrics.rsi14 || 50;

        // RSI mean reversion
        if (rsi > 70) {
            // Overbought - expect pullback
            return -0.002 * ((rsi - 70) / 30);
        } else if (rsi < 30) {
            // Oversold - expect bounce
            return 0.002 * ((30 - rsi) / 30);
        }

        // Neutral RSI - slight momentum continuation
        return (rsi - 50) / 5000;
    }

    /**
     * Volatility signal - high vol = wider expected range
     */
    volatilitySignal(metrics) {
        // Volatility doesn't predict direction, but affects confidence
        // Return 0 for direction but this is used for range estimation
        return 0;
    }

    /**
     * VWAP signal - price relative to VWAP suggests mean reversion
     */
    vwapSignal(metrics) {
        const price = metrics.price;
        const vwap = metrics.vwap;

        if (!vwap || vwap === 0) return 0;

        const deviation = (price - vwap) / vwap;

        // Mean reversion toward VWAP
        return -deviation * 0.3;
    }

    /**
     * Trend signal - longer-term trend direction
     */
    trendSignal(metrics) {
        const change15m = metrics.priceChange15m || 0;

        // Trend continuation with dampening
        return change15m * 0.5;
    }

    /**
     * Calculate agreement between signals
     */
    calculateAgreement(signalValues) {
        if (signalValues.length < 2) return 0.5;

        const positive = signalValues.filter(v => v > 0).length;
        const negative = signalValues.filter(v => v < 0).length;
        const total = signalValues.length;

        // Agreement = how many signals point the same way
        const maxAgreement = Math.max(positive, negative);
        return maxAgreement / total;
    }

    /**
     * Calculate probability distribution across price buckets
     */
    calculateBucketProbabilities(currentPrice, predictedPrice, volatility, bucketSize) {
        const probabilities = {};

        // Standard deviation for 15 minutes based on current volatility
        const stdDev = currentPrice * Math.max(0.002, volatility) * Math.sqrt(15);

        // Calculate probabilities for buckets around predicted price
        const centerBucket = Math.floor(predictedPrice / bucketSize) * bucketSize;

        for (let offset = -3; offset <= 3; offset++) {
            const bucket = centerBucket + offset * bucketSize;
            const bucketCenter = bucket + bucketSize / 2;

            // Simple normal distribution approximation
            const zScore = (bucketCenter - predictedPrice) / stdDev;
            const probability = Math.exp(-0.5 * zScore * zScore) / (stdDev * Math.sqrt(2 * Math.PI)) * bucketSize;

            probabilities[bucket] = Math.round(probability * 1000) / 10; // As percentage
        }

        // Normalize to 100%
        const total = Object.values(probabilities).reduce((a, b) => a + b, 0);
        for (const bucket in probabilities) {
            probabilities[bucket] = Math.round(probabilities[bucket] / total * 1000) / 10;
        }

        return probabilities;
    }

    /**
     * Get trading recommendation based on prediction
     */
    getRecommendation(expectedMove, confidence, bucketProbabilities) {
        // Find highest probability bucket
        const bestBucket = Object.entries(bucketProbabilities)
            .sort((a, b) => b[1] - a[1])[0];

        const recommendation = {
            action: 'HOLD',
            bucket: parseInt(bestBucket[0]),
            probability: bestBucket[1],
            size: 0,
            reason: '',
        };

        // Only trade with sufficient confidence and edge
        if (confidence < 50) {
            recommendation.reason = 'Low confidence - signals disagreeing';
            return recommendation;
        }

        if (bestBucket[1] < 25) {
            recommendation.reason = 'No clear bucket - high uncertainty';
            return recommendation;
        }

        // Calculate edge
        // If Polymarket price is below our probability, we have edge
        const edge = bestBucket[1] - 25; // Assume market prices at ~25% for middle bucket

        if (edge > 10) {
            recommendation.action = 'BUY';
            recommendation.size = Math.min(50, edge * 2); // $1-2 per % of edge
            recommendation.reason = `${edge.toFixed(1)}% edge on ${bestBucket[0]} bucket`;
        }

        return recommendation;
    }

    /**
     * Record prediction for accuracy tracking
     */
    recordPrediction(prediction) {
        this.predictions.push({
            ...prediction,
            resolved: false,
            actualPrice: null,
        });

        // Keep only last 100 predictions
        if (this.predictions.length > 100) {
            this.predictions.shift();
        }
    }

    /**
     * Resolve a prediction and update accuracy
     */
    resolvePrediction(timestamp, actualPrice) {
        const prediction = this.predictions.find(p => p.timestamp === timestamp);
        if (!prediction || prediction.resolved) return;

        prediction.resolved = true;
        prediction.actualPrice = actualPrice;

        const error = Math.abs(prediction.predictedPrice - actualPrice) / actualPrice;
        const predictedBucket = prediction.predictedBucket;
        const actualBucket = Math.floor(actualPrice / 500) * 500;
        const correct = predictedBucket === actualBucket;

        this.accuracy.total++;
        if (correct) this.accuracy.correct++;
        this.accuracy.avgError = (this.accuracy.avgError * (this.accuracy.total - 1) + error) / this.accuracy.total;

        return {
            predicted: prediction.predictedPrice,
            actual: actualPrice,
            error: (error * 100).toFixed(2) + '%',
            bucketCorrect: correct,
        };
    }

    /**
     * Get accuracy statistics
     */
    getAccuracy() {
        return {
            totalPredictions: this.accuracy.total,
            correctBuckets: this.accuracy.correct,
            bucketAccuracy: this.accuracy.total > 0
                ? (this.accuracy.correct / this.accuracy.total * 100).toFixed(1) + '%'
                : 'N/A',
            avgError: (this.accuracy.avgError * 100).toFixed(3) + '%',
        };
    }
}

module.exports = { BTCPredictor };
