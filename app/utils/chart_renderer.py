import io
from datetime import datetime
from typing import Optional

import numpy as np
import pytz

from app.utils.theme import Theme

matplotlib = None
plt = None
FancyBboxPatch = None


def _ensure_matplotlib():
    """Matplotlib's font-cache scan on first `import matplotlib.pyplot` can
    take real time on a cold filesystem - it must never run at module import
    time, only when a chart is actually rendered (Telegram bot only). Doing
    it at import time blocked Uvicorn from ever binding a port on FastAPI
    Cloud, which showed up as an unexplained "verification failed" with zero
    runtime logs, since the process never got far enough to log anything."""
    global matplotlib, plt, FancyBboxPatch
    if plt is not None:
        return
    import matplotlib as _matplotlib
    _matplotlib.use('Agg')
    import matplotlib.pyplot as _plt
    from matplotlib.patches import FancyBboxPatch as _FancyBboxPatch
    matplotlib = _matplotlib
    plt = _plt
    FancyBboxPatch = _FancyBboxPatch


class ChartRenderer:
    """
    Renders premium, TradingView-style dark mode charts.
    """
    def __init__(self, tz_name: str = "Asia/Phnom_Penh"):
        _ensure_matplotlib()
        self.tz = pytz.timezone(tz_name)
        self.theme = Theme()

    def _setup_figure(self, width=10, height=5):
        """Initializes a dark-mode figure with custom gridspec."""
        plt.style.use('dark_background')
        fig = plt.figure(figsize=(width, height), dpi=120)
        
        # Create a gradient background manually
        ax_bg = fig.add_axes([0, 0, 1, 1])
        ax_bg.set_axis_off()
        self._draw_gradient_background(ax_bg)
        
        return fig

    def _draw_gradient_background(self, ax):
        """Draws a vertical gradient from dark blue-grey to black."""
        y = np.linspace(0, 1, 100)
        X = np.array([y, y])
        
        # Custom colormap from Theme colors
        cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
            "custom_dark", [self.theme.background_btm, self.theme.background_top]
        )
        ax.imshow(X, extent=[0, 1, 0, 1], aspect='auto', cmap=cmap, origin='lower')

    def _generate_realistic_series(self, current_price: float, change: float, n_points: int = 100):
        """Generates a realistic price path ending at current_price."""
        start_price = current_price - change
        volatility = current_price * 0.02  # 2% volatility
        
        # Random walk with drift
        x = np.linspace(0, 1, n_points)
        drift = np.linspace(start_price, current_price, n_points)
        noise = np.cumsum(np.random.normal(0, volatility / np.sqrt(n_points), n_points))
        
        # Normalize noise to start and end at 0 so curve hits exact targets
        noise = noise - noise[0] # Start at 0
        noise = noise - x * noise[-1] # End at 0 (Brownian Bridge)
        
        return drift + noise

    def price_card(self, ticker: str, price: float, change: float, change_direction: str = "equal", 
                   series: Optional[list] = None, stats: Optional[dict] = None) -> io.BytesIO:
        """
        Premium 'Key Statistics' Dashboard.
        Layout: 
        [ Left: Chart (70%) ] [ Right: Stats Panel (30%) ]
        """
        fig = self._setup_figure(12, 6)
        
        # Layout: GridSpec
        # Left side (Chart) gets 2/3 width, Right side (Stats) gets 1/3
        gs = fig.add_gridspec(1, 3, wspace=0.05)
        ax_chart = fig.add_subplot(gs[0, :2]) # Spans first 2 cols
        ax_stats = fig.add_subplot(gs[0, 2])  # Spans last col

        # --- 1. The Chart (Left) ---
        ax_chart.set_facecolor("none") # Transparent to show gradient
        
        # Determine Color
        direction = (change_direction or "equal").lower()
        if direction == "up":
            color = self.theme.up_color
            glow = self.theme.up_glow
            arrow = "▲"
        elif direction == "down":
            color = self.theme.down_color
            glow = self.theme.down_glow
            arrow = "▼"
        else:
            color = self.theme.text_primary
            glow = "#ffffff20"
            arrow = "●"

        # Generate Data if missing
        if not series:
            prices = self._generate_realistic_series(price, change)
        else:
            prices = np.array(series)
        
        x = np.arange(len(prices))

        # Main Line
        ax_chart.plot(x, prices, color=color, linewidth=2.5, alpha=1.0)
        
        # "Glow" effect (fill below)
        ax_chart.fill_between(x, prices, prices.min(), color=glow, alpha=0.15)
        ax_chart.fill_between(x, prices, prices.min(), color=glow, alpha=0.05) # Double layer for depth

        # Sparkline Styling (No grid, no axes)
        ax_chart.grid(False)
        for spine in ax_chart.spines.values():
            spine.set_visible(False)
        ax_chart.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

        # Header Text (Inside Chart Area)
        # Ticker & Company Name
        ax_chart.text(0.02, 0.95, f"{ticker.upper()}", transform=ax_chart.transAxes, 
                      fontsize=24, fontweight='bold', color=self.theme.text_primary, ha='left')
        ax_chart.text(0.18, 0.95, "CSX Listed", transform=ax_chart.transAxes, 
                      fontsize=14, color=self.theme.text_secondary, ha='left', va='center')

        # Big Price
        price_fmt = f"{price:,.0f} KHR"
        ax_chart.text(0.02, 0.85, price_fmt, transform=ax_chart.transAxes, 
                      fontsize=38, fontweight='bold', color=color, ha='left')

        # Change %
        pct = (change / (price - change) * 100) if (price-change) != 0 else 0
        sign = "+" if change > 0 else ""
        chg_fmt = f"{arrow} {sign}{change:,.0f} ({sign}{pct:.2f}%)"
        ax_chart.text(0.02, 0.76, chg_fmt, transform=ax_chart.transAxes, 
                      fontsize=16, fontweight='bold', color=color, ha='left')

        # --- 2. Key Statistics Panel (Right) ---
        ax_stats.set_axis_off()
        
        # Panel Title
        ax_stats.text(0.05, 0.95, "Key Statistics", transform=ax_stats.transAxes,
                      fontsize=16, fontweight='bold', color=self.theme.text_primary)
        ax_stats.plot([0.05, 0.95], [0.91, 0.91], color=self.theme.grid_line, lw=1, transform=ax_stats.transAxes)

        # Default Stats (Simulation for visual completeness if not provided)
        if not stats:
            prev_close = price - change
            open_p = prev_close + (change * 0.2)
            high_p = max(price, open_p) * 1.01
            low_p = min(price, open_p) * 0.99
            
            stats = {
                "Open": f"{open_p:,.0f} KHR",
                "High": f"{high_p:,.0f} KHR",
                "Low": f"{low_p:,.0f} KHR",
                "Prev. Close": f"{prev_close:,.0f} KHR",
                "Volume": f"{int(price/100 * np.random.rand() * 1000):,} K",
                "Mkt Cap": "N/A"
            }

        # Render List
        y_pos = 0.82
        for key, value in stats.items():
            # Label (Left)
            ax_stats.text(0.05, y_pos, key, transform=ax_stats.transAxes,
                          fontsize=12, color=self.theme.text_secondary, ha='left')
            # Value (Right)
            ax_stats.text(0.95, y_pos, value, transform=ax_stats.transAxes,
                          fontsize=12, fontweight='bold', color=self.theme.text_primary, ha='right')
            
            # Separator Line
            ax_stats.plot([0.05, 0.95], [y_pos - 0.04, y_pos - 0.04], 
                          color=self.theme.grid_line, lw=0.5, ls=":", transform=ax_stats.transAxes)
            
            y_pos -= 0.10

        # "To The Moon" Logic
        if direction == "up" and pct > 2.0:
             ax_stats.text(0.5, 0.1, "🚀 TO THE MOON!", transform=ax_stats.transAxes,
                          fontsize=14, fontweight='bold', color="#00E396", ha='center',
                          bbox=dict(facecolor='#00E39620', edgecolor='#00E396', boxstyle='round,pad=0.5'))

        # Timestamp Footer
        ts = datetime.now(self.tz).strftime("%d %b %Y %H:%M")
        ax_chart.text(0.98, -0.1, f"Generated: {ts}", transform=ax_chart.transAxes,
                      fontsize=10, color=self.theme.text_secondary, ha='right', style='italic')

        return self._save(fig)
    
    def trade_card(self, ticker: str, side: str, price: int, qty: int, commission: int, seq: int) -> io.BytesIO:
        """Updated Trade Confirmation Card to match Dark Theme."""
        fig = self._setup_figure(8, 5)
        ax = fig.add_subplot(111)
        ax.set_axis_off()
        
        # Color Logic
        is_buy = side.upper() == "BUY"
        color = self.theme.up_color if is_buy else self.theme.down_color
        bg_pill = self.theme.up_glow if is_buy else self.theme.down_glow
        
        # Title with Pill Background
        ax.add_patch(FancyBboxPatch((0.35, 0.82), 0.3, 0.12, boxstyle="round,pad=0.02", 
                                    facecolor=bg_pill, edgecolor=color, transform=ax.transAxes))
        ax.text(0.5, 0.88, f"{side.upper()} CONFIRMED", ha="center", va="center", 
                fontsize=20, fontweight="bold", color=color, transform=ax.transAxes)
        
        # Big Ticker
        ax.text(0.5, 0.70, ticker.upper(), ha="center", fontsize=36, fontweight='bold', 
                color=self.theme.text_primary, transform=ax.transAxes)

        # Data Grid
        details = [
            ("Price", f"{price:,} KHR"),
            ("Quantity", f"{qty:,}"),
            ("Total Value", f"{price * qty:,} KHR"),
            ("Commission", f"{commission:,} KHR"),
            ("Order ID", f"#{seq}")
        ]
        
        y = 0.55
        for label, val in details:
            ax.text(0.25, y, label, ha="left", fontsize=12, color=self.theme.text_secondary, transform=ax.transAxes)
            ax.text(0.75, y, val, ha="right", fontsize=12, fontweight="bold", color=self.theme.text_primary, transform=ax.transAxes)
            ax.plot([0.25, 0.75], [y-0.02, y-0.02], color=self.theme.grid_line, lw=1, ls=":", transform=ax.transAxes)
            y -= 0.10

        return self._save(fig)

    def position_card(self, ticker: str, pos: dict) -> io.BytesIO:
        """Renders a position detail card."""
        fig = self._setup_figure(10, 6)
        ax = fig.add_subplot(111)
        ax.set_axis_off()
        
        # Title
        ax.text(0.5, 0.90, f"{ticker.upper()} POSITION", ha="center", fontsize=24, 
                fontweight='bold', color=self.theme.text_primary, transform=ax.transAxes)
        ax.plot([0.1, 0.9], [0.87, 0.87], color=self.theme.grid_line, lw=1, transform=ax.transAxes)
        
        # Position Details
        total_bought = pos.get('totalBoughtQty', 0)
        total_sold = pos.get('totalSoldQty', 0)
        remaining = pos.get('remainingQty', 0)
        sold_pct = pos.get('soldPercent', 0)
        num_lots = len(pos.get('remainingLots', []))
        
        details = [
            ("Total Bought", f"{total_bought:,}"),
            ("Total Sold", f"{total_sold:,}"),
            ("Remaining Qty", f"{remaining:,}"),
            ("Sold %", f"{sold_pct:.1f}%"),
            ("Open Lots", f"{num_lots}"),
        ]
        
        y = 0.75
        for label, value in details:
            ax.text(0.15, y, label, ha="left", fontsize=12, color=self.theme.text_secondary, transform=ax.transAxes)
            ax.text(0.85, y, value, ha="right", fontsize=12, fontweight='bold', color=self.theme.text_primary, transform=ax.transAxes)
            ax.plot([0.15, 0.85], [y-0.03, y-0.03], color=self.theme.grid_line, lw=0.5, ls=":", transform=ax.transAxes)
            y -= 0.12
        
        # Show remaining lots details
        if pos.get('remainingLots'):
            y -= 0.05
            ax.text(0.15, y, "Remaining Lots:", ha="left", fontsize=11, fontweight='bold',
                   color=self.theme.text_secondary, transform=ax.transAxes)
            y -= 0.08
            for lot in pos['remainingLots'][:3]:
                lot_text = f"Seq #{lot['seq']}: {lot['qtyOpen']}@{lot['price']:,}"
                ax.text(0.20, y, lot_text, ha="left", fontsize=10, 
                       color=self.theme.text_primary, transform=ax.transAxes)
                y -= 0.06
        
        return self._save(fig)

    def portfolio_card(self, uid: str, rows: list) -> io.BytesIO:
        """Renders a portfolio dashboard with all positions."""
        fig = self._setup_figure(12, 8)
        ax = fig.add_subplot(111)
        ax.set_axis_off()
        
        # Title
        ax.text(0.5, 0.95, "PORTFOLIO DASHBOARD", ha="center", fontsize=20, 
                fontweight='bold', color=self.theme.text_primary, transform=ax.transAxes)
        ax.plot([0.05, 0.95], [0.92, 0.92], color=self.theme.grid_line, lw=1, transform=ax.transAxes)
        
        # Header Row
        headers = ["Ticker", "Qty", "Avg Cost", "Current", "Unrealised P&L"]
        col_x = [0.08, 0.25, 0.40, 0.60, 0.80]
        for i, header in enumerate(headers):
            ax.text(col_x[i], 0.88, header, ha="left", fontsize=11, fontweight='bold', 
                   color=self.theme.text_secondary, transform=ax.transAxes)
        
        # Data Rows
        y = 0.83
        for row in rows[:10]:
            ticker = row.get('ticker', 'N/A')
            qty = row.get('remainingQty', 0)
            avg_cost = row.get('avgCostRemaining', 0)
            current = row.get('lastPrice', 0)
            pnl = int(row.get('unrealisedPnl', 0))
            pnl_color = self.theme.up_color if pnl >= 0 else self.theme.down_color
            
            ax.text(col_x[0], y, ticker, ha="left", fontsize=10, color=self.theme.text_primary, transform=ax.transAxes)
            ax.text(col_x[1], y, f"{qty:,}", ha="left", fontsize=10, color=self.theme.text_secondary, transform=ax.transAxes)
            ax.text(col_x[2], y, f"{avg_cost:,.0f}" if avg_cost else "N/A", ha="left", fontsize=10, color=self.theme.text_secondary, transform=ax.transAxes)
            ax.text(col_x[3], y, f"{current:,}" if current else "N/A", ha="left", fontsize=10, color=self.theme.text_secondary, transform=ax.transAxes)
            ax.text(col_x[4], y, f"{pnl:+,}", ha="left", fontsize=10, fontweight='bold', color=pnl_color, transform=ax.transAxes)
            
            ax.plot([0.05, 0.95], [y-0.025, y-0.025], color=self.theme.grid_line, lw=0.3, ls=":", transform=ax.transAxes)
            y -= 0.07
        
        return self._save(fig)

    def all_stocks_card(self, stocks: list, sparklines: dict = None) -> io.BytesIO:
        """Renders a market overview card showing all stocks."""
        sparklines = sparklines or {}
        
        processed_stocks = []
        for stock in stocks:
            s = dict(stock)
            change = s.get('change', 0)
            price = s.get('price', 0)
            change_dir = s.get('change_direction', 'equal')
            
            if change_dir == 'down' and change > 0:
                change = -change
            elif change_dir == 'up' and change < 0:
                change = -change
                
            s['change'] = change
            s['change_pct'] = (change / (price - change) * 100) if (price - change) != 0 and change != 0 else 0
            processed_stocks.append(s)
            
        processed_stocks.sort(key=lambda x: x.get('change_pct', 0), reverse=True)
        stocks = processed_stocks
        
        num_stocks = min(len(stocks), 15)
        fig_height = max(4.0, num_stocks * 0.9 + 2)
        fig = self._setup_figure(8, fig_height)
        ax = fig.add_subplot(111)
        ax.set_axis_off()
        
        # Title
        ax.text(0.5, 0.95, "MARKET OVERVIEW", ha="center", fontsize=22, 
                fontweight='bold', color=self.theme.text_primary, transform=ax.transAxes)
        
        row_step = 0.88 / max(num_stocks, 1)
        if row_step > 0.15:
            row_step = 0.15
            
        y = 0.90 - (row_step / 2)
        
        for stock in stocks[:15]:
            symbol = stock.get('ticker', 'N/A')
            price = stock.get('price', 0)
            change = stock.get('change', 0)
            if change > 0:
                change_color = self.theme.up_color
                change_sign = "+"
            elif change < 0:
                change_color = self.theme.down_color
                change_sign = "-"
            else:
                change_color = self.theme.text_secondary
                change_sign = ""
                
            change_pct = stock.get('change_pct', 0)
            
            # Card background
            box_height = row_step * 0.8
            box_y = y - (box_height / 2)
            
            box = FancyBboxPatch((0.05, box_y), 0.9, box_height, boxstyle="round,pad=0.01",
                                 facecolor=self.theme.background_top, edgecolor=self.theme.grid_line, 
                                 linewidth=1, transform=ax.transAxes)
            ax.add_patch(box)
            
            # Left: Ticker
            ax.text(0.1, y, symbol, ha="left", va="center", fontsize=15, fontweight='bold', 
                   color=self.theme.text_primary, transform=ax.transAxes)
                   
            # Middle: Sparkline
            sparkline_data = sparklines.get(symbol)
            if sparkline_data and len(sparkline_data) > 1:
                min_p = min(sparkline_data)
                max_p = max(sparkline_data)
                range_p = max_p - min_p if max_p > min_p else 1
                norm_data = [(p - min_p) / range_p for p in sparkline_data]
                
                # X range: [0.3, 0.7], Y range: [y - box_height*0.3, y + box_height*0.3]
                x_coords = [0.3 + (0.4 * i / (len(norm_data) - 1)) for i in range(len(norm_data))]
                y_coords = [y - (box_height * 0.3) + (box_height * 0.6 * p) for p in norm_data]
                
                ax.plot(x_coords, y_coords, color=change_color, lw=2.5, transform=ax.transAxes)
                ax.fill_between(x_coords, [y - (box_height * 0.3)] * len(x_coords), y_coords, 
                              color=change_color, alpha=0.1, transform=ax.transAxes)
                   
            # Right: Price (Top) and Change (Bottom)
            price_text = f"{price:,.0f} riel"
            ax.text(0.9, y + (row_step * 0.15), price_text, ha="right", va="center", fontsize=13, fontweight='bold', 
                   color=self.theme.text_primary, transform=ax.transAxes)
                   
            change_text = f"{change_sign}{abs(change):,.0f} ({change_pct:+.2f}%)"
            ax.text(0.9, y - (row_step * 0.2), change_text, ha="right", va="center", fontsize=12, fontweight='bold',
                   color=change_color, transform=ax.transAxes)
            
            y -= row_step
        
        return self._save(fig)

    def stock_detail_card(self, ticker: str, buys: list, sells: list, allocs: list, summary: dict) -> io.BytesIO:
        """Renders a detailed stock card with LIFO allocation visualization."""
        fig = self._setup_figure(14, 10)
        
        gs = fig.add_gridspec(2, 2, height_ratios=[1, 1.5], width_ratios=[1, 1], 
                             hspace=0.15, wspace=0.1)
        ax_summary = fig.add_subplot(gs[0, :])
        ax_buys = fig.add_subplot(gs[1, 0])
        ax_sells = fig.add_subplot(gs[1, 1])
        
        # --- Summary Panel (Top) ---
        ax_summary.set_axis_off()
        ax_summary.text(0.5, 0.95, f"{ticker.upper()} - STOCK DETAILS (Lowest Price First)", 
                       ha="center", fontsize=22, fontweight='bold', 
                       color=self.theme.text_primary, transform=ax_summary.transAxes)
        ax_summary.plot([0.05, 0.95], [0.90, 0.90], color=self.theme.grid_line, 
                       lw=2, transform=ax_summary.transAxes)
        
        total_bought = summary.get('totalBought', 0)
        total_sold = summary.get('totalSold', 0)
        remaining = summary.get('remaining', 0)
        realised_pnl = summary.get('realisedPnl', 0)
        
        pnl_color = self.theme.up_color if realised_pnl >= 0 else self.theme.down_color
        
        stats = [
            ("Total Bought", f"{total_bought:,}", self.theme.text_primary),
            ("Total Sold", f"{total_sold:,}", self.theme.text_primary),
            ("Remaining", f"{remaining:,}", self.theme.text_primary),
            ("Realised P/L", f"{realised_pnl:+,} KHR", pnl_color),
        ]
        
        x_pos = 0.15
        for label, value, color in stats:
            ax_summary.text(x_pos, 0.70, label, ha="center", fontsize=11, 
                          color=self.theme.text_secondary, transform=ax_summary.transAxes)
            ax_summary.text(x_pos, 0.50, value, ha="center", fontsize=16, 
                          fontweight='bold', color=color, transform=ax_summary.transAxes)
            x_pos += 0.20
        
        # --- BUY Orders Panel (Bottom Left) ---
        ax_buys.set_facecolor("none")
        ax_buys.set_xlim(0, 1)
        ax_buys.set_ylim(0, 1)
        ax_buys.axis('off')
        
        ax_buys.text(0.5, 0.95, "BUY ORDERS (Lowest Price First)", 
                ha="center", fontsize=12, fontweight='bold', 
                color=self.theme.up_color, transform=ax_buys.transAxes)
        ax_buys.plot([0.05, 0.95], [0.92, 0.92], color=self.theme.grid_line, 
                    lw=1, transform=ax_buys.transAxes)
        
        headers = ["Seq", "Qty@Price", "Remaining"]
        col_x = [0.08, 0.35, 0.70]
        y = 0.88
        for i, h in enumerate(headers):
            ax_buys.text(col_x[i], y, h, ha="left", fontsize=9, fontweight='bold',
                        color=self.theme.text_secondary, transform=ax_buys.transAxes)
        
        y = 0.82
        for buy in buys[:8]:
            seq = buy.get('seq', '?')
            qty = buy.get('qty', 0)
            price = buy.get('price', 0)
            remaining = buy.get('remaining', 0)
            
            status = "OPEN" if remaining > 0 else "SOLD"
            status_color = self.theme.up_color if remaining > 0 else self.theme.text_secondary
            
            ax_buys.text(col_x[0], y, f"{status} #{seq}", ha="left", fontsize=9,
                        color=status_color, transform=ax_buys.transAxes)
            ax_buys.text(col_x[1], y, f"{qty}@{price:,}", ha="left", fontsize=9,
                        color=self.theme.text_secondary, transform=ax_buys.transAxes)
            ax_buys.text(col_x[2], y, f"{remaining}", ha="left", fontsize=9,
                        fontweight='bold' if remaining > 0 else 'normal',
                        color=self.theme.up_color if remaining > 0 else self.theme.text_secondary,
                        transform=ax_buys.transAxes)
            
            ax_buys.plot([0.05, 0.95], [y-0.03, y-0.03], color=self.theme.grid_line, 
                        lw=0.3, ls=":", transform=ax_buys.transAxes)
            y -= 0.09
        
        # --- SELL Orders Panel (Bottom Right) ---
        ax_sells.set_facecolor("none")
        ax_sells.set_xlim(0, 1)
        ax_sells.set_ylim(0, 1)
        ax_sells.axis('off')
        
        ax_sells.text(0.5, 0.95, "SELL ORDERS", 
                 ha="center", fontsize=12, fontweight='bold', 
                 color=self.theme.down_color, transform=ax_sells.transAxes)
        ax_sells.plot([0.05, 0.95], [0.92, 0.92], color=self.theme.grid_line, 
                     lw=1, transform=ax_sells.transAxes)
        
        headers = ["Seq", "Qty@Price", "P/L"]
        y = 0.88
        for i, h in enumerate(headers):
            ax_sells.text(col_x[i], y, h, ha="left", fontsize=9, fontweight='bold',
                         color=self.theme.text_secondary, transform=ax_sells.transAxes)
        
        y = 0.82
        for sell in sells[:8]:
            seq = sell.get('seq', '?')
            qty = sell.get('qty', 0)
            price = sell.get('price', 0)
            pnl = sell.get('pnl', 0)
            matched = sell.get('matched', [])
            
            pnl_color = self.theme.up_color if pnl >= 0 else self.theme.down_color
            
            ax_sells.text(col_x[0], y, f"#{seq}", ha="left", fontsize=9,
                         color=self.theme.text_primary, transform=ax_sells.transAxes)
            ax_sells.text(col_x[1], y, f"{qty}@{price:,}", ha="left", fontsize=9,
                         color=self.theme.text_secondary, transform=ax_sells.transAxes)
            ax_sells.text(col_x[2], y, f"{pnl:+,}", ha="left", fontsize=9,
                         fontweight='bold', color=pnl_color, transform=ax_sells.transAxes)
            
            ax_sells.plot([0.05, 0.95], [y-0.03, y-0.03], color=self.theme.grid_line, 
                         lw=0.3, ls=":", transform=ax_sells.transAxes)
            y -= 0.04
            
            for match in matched[:2]:
                buy_seq = match.get('buySeq', '?')
                match_qty = match.get('qty', 0)
                match_price = match.get('price', 0)
                
                ax_sells.text(0.12, y, f"-> #{buy_seq}: {match_qty}@{match_price:,}", 
                            ha="left", fontsize=7, color=self.theme.text_secondary,
                            transform=ax_sells.transAxes, style='italic')
                y -= 0.04
            
            y -= 0.01
        
        return self._save(fig)

    def rankings_card(self, title: str, ranked: list, is_tickers: bool = False) -> io.BytesIO:
        """Renders a rankings card for top orders or tickers."""
        fig = self._setup_figure(10, 8)
        ax = fig.add_subplot(111)
        ax.set_axis_off()
        
        # Title
        ax.text(0.5, 0.95, title, ha="center", fontsize=20, 
                fontweight='bold', color=self.theme.text_primary, transform=ax.transAxes)
        ax.plot([0.05, 0.95], [0.92, 0.92], color=self.theme.grid_line, lw=1, transform=ax.transAxes)
        
        # Headers
        if is_tickers:
            headers = ["Ticker", "Realised P&L", "Total Qty"]
            col_x = [0.10, 0.50, 0.75]
        else:
            headers = ["Order", "Realised P&L", "Qty"]
            col_x = [0.10, 0.50, 0.75]
        
        for i, header in enumerate(headers):
            ax.text(col_x[i], 0.88, header, ha="left", fontsize=11, fontweight='bold', 
                   color=self.theme.text_secondary, transform=ax.transAxes)
        
        # Data Rows
        y = 0.83
        for i, item in enumerate(ranked[:5], 1):
            pnl = item.get('realisedPnl', 0)
            pnl_color = self.theme.up_color if pnl >= 0 else self.theme.down_color
            
            if is_tickers:
                name = item.get('ticker', 'N/A')
                qty = item.get('totalQty', 0)
            else:
                name = f"#{item.get('seq', i)}"
                qty = item.get('qty', 0)
            
            ax.text(col_x[0], y, f"{i}. {name}", ha="left", fontsize=11, fontweight='bold', 
                   color=self.theme.text_primary, transform=ax.transAxes)
            ax.text(col_x[1], y, f"{pnl:+,} KHR", ha="left", fontsize=11, fontweight='bold', 
                   color=pnl_color, transform=ax.transAxes)
            ax.text(col_x[2], y, f"{qty:,}", ha="left", fontsize=10, color=self.theme.text_secondary, transform=ax.transAxes)
            
            ax.plot([0.05, 0.95], [y-0.03, y-0.03], color=self.theme.grid_line, lw=0.5, ls=":", transform=ax.transAxes)
            y -= 0.12
        
        return self._save(fig)

    def _save(self, fig) -> io.BytesIO:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor(), dpi=120)
        plt.close(fig)
        buf.seek(0)
        return buf
