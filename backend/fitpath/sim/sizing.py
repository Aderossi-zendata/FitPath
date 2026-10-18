"""Traffic presets for the 2,000 m² store: how many people walk in on an average day, and how many of them buy.

The H&M data has no store id, so it cannot tell the volume of a single store. It does give the
shape over time: each simulated day multiplies a draw from the preset's range by the real daily
index (Black Friday ~4.6x, a November Monday ~0.4x).

Ranges are calibrated on the few public references found (October 2026):
- Placer.ai: the busiest US Zara (Yonkers, NY) had ~76.8K visits in December 2024, ~2,500/day in its peak month
- apparel store conversion (visitors -> receipts) is usually quoted at 15-40%, most often 20-30%
They are assumptions to replace with people-counter data from a real store.
"""

SIZES = {
    "low": {"label": "Low traffic", "area": "2,000 m² in a mid-size town", "visitors": (700, 1200), "conversion": (.22, .32)},
    "medium": {"label": "Medium traffic", "area": "2,000 m² in a shopping centre", "visitors": (1200, 2000), "conversion": (.20, .30)},
    "high": {"label": "High traffic", "area": "2,000 m² on a high street", "visitors": (2000, 3200), "conversion": (.18, .26)},
}

SOURCES = [
    {"label": "Placer.ai · Zara, visits per store", "url": "https://www.placer.ai/free-tools/chains/zara"},
    {"label": "Flame Analytics · conversion in physical retail", "url": "https://www.flameanalytics.com/en/en-blog-conversion-analytics-retail-measure-optimize/"},
    {"label": "LSBU · footfall, attraction and conversion", "url": "https://openresearch.lsbu.ac.uk/item/86yw8"},
]
