/**
 * Chart management using TradingView Lightweight Charts
 */

class ChartManager {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.chart = null;
        this.candleSeries = null;
        this.volumeSeries = null;
        this.currentTimeframe = '15m';
    }

    initialize() {
        if (!this.container) {
            console.error('Chart container not found');
            return;
        }

        // Create chart
        this.chart = LightweightCharts.createChart(this.container, {
            layout: {
                background: { type: 'solid', color: '#1f2937' },
                textColor: '#9ca3af',
            },
            grid: {
                vertLines: { color: '#374151' },
                horzLines: { color: '#374151' },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
            },
            rightPriceScale: {
                borderColor: '#374151',
            },
            timeScale: {
                borderColor: '#374151',
                timeVisible: true,
                secondsVisible: false,
            },
            handleScroll: {
                mouseWheel: true,
                pressedMouseMove: true,
            },
            handleScale: {
                axisPressedMouseMove: true,
                mouseWheel: true,
                pinch: true,
            },
        });

        // Add candlestick series
        this.candleSeries = this.chart.addCandlestickSeries({
            upColor: '#22c55e',
            downColor: '#ef4444',
            borderUpColor: '#22c55e',
            borderDownColor: '#ef4444',
            wickUpColor: '#22c55e',
            wickDownColor: '#ef4444',
        });

        // Add volume series
        this.volumeSeries = this.chart.addHistogramSeries({
            color: '#6366f1',
            priceFormat: {
                type: 'volume',
            },
            priceScaleId: '',
            scaleMargins: {
                top: 0.8,
                bottom: 0,
            },
        });

        // Handle resize
        window.addEventListener('resize', () => {
            if (this.chart) {
                this.chart.applyOptions({
                    width: this.container.clientWidth,
                });
            }
        });

        // Initial resize
        this.chart.applyOptions({
            width: this.container.clientWidth,
        });
    }

    async loadData(timeframe) {
        this.currentTimeframe = timeframe;

        try {
            const response = await fetch(`/api/price/history?tf=${timeframe}&limit=200`);
            const result = await response.json();

            if (result.data && result.data.length > 0) {
                // Format data for candlestick series
                const candleData = result.data.map(d => ({
                    time: d.time,
                    open: d.open,
                    high: d.high,
                    low: d.low,
                    close: d.close,
                }));

                // Format data for volume series
                const volumeData = result.data.map(d => ({
                    time: d.time,
                    value: d.volume,
                    color: d.close >= d.open ? '#22c55e40' : '#ef444440',
                }));

                this.candleSeries.setData(candleData);
                this.volumeSeries.setData(volumeData);

                // Fit content
                this.chart.timeScale().fitContent();
            }
        } catch (error) {
            console.error('Failed to load chart data:', error);
        }
    }

    updateCandle(candle) {
        if (this.candleSeries && candle) {
            this.candleSeries.update({
                time: candle.time,
                open: candle.open,
                high: candle.high,
                low: candle.low,
                close: candle.close,
            });
        }
    }

    addMarker(time, type, text) {
        if (!this.candleSeries) return;

        const markers = [{
            time: time,
            position: type === 'buy' ? 'belowBar' : 'aboveBar',
            color: type === 'buy' ? '#22c55e' : '#ef4444',
            shape: type === 'buy' ? 'arrowUp' : 'arrowDown',
            text: text,
        }];

        this.candleSeries.setMarkers(markers);
    }

    clearMarkers() {
        if (this.candleSeries) {
            this.candleSeries.setMarkers([]);
        }
    }

    destroy() {
        if (this.chart) {
            this.chart.remove();
            this.chart = null;
        }
    }
}

// Global chart manager instance
let chartManager = null;
