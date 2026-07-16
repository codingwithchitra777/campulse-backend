"""Webhook-driven port of campulse-bot. Parses a Telegram command message and
replies via the Bot API (telegram_client), reusing the backend's existing
services + ChartRenderer. Stateless: every command is a single message, no
conversation state — so it maps cleanly onto webhook dispatch."""
import re
import logging
from typing import Optional, Tuple

from app.repositories.trade import TradeRepository
from app.repositories.allocation import AllocationRepository
from app.repositories.user import UserRepository
from app.repositories.link import LinkRepository
from app.services.pricing import pricing_service_instance
from app.services.portfolio import PortfolioService
from app.services.redis_service import RedisService
from app.services.trade_service import record_trade
from app.utils.chart_renderer import ChartRenderer
from app.services import telegram_client

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "\U0001F680 CamPulse ✅\n\n"
    "\U0001F4CA /price$ABC or /show_all\n"
    "\U0001F4BC /buy$ABC 7300 100\n"
    "\U0001F4BC /sell$ABC 7400 100\n"
    "\U0001F4C8 /portfolio, /position ABC\n"
    "\U0001F4CB /stock ABC - Stock details (Lowest Price)\n"
    "\U0001F3C6 /top_orders, /top_tickers"
)


class CommandParser:
    """Extracts command + args from raw Telegram message text. Handles both the
    `$`-prefixed syntax (/price$ABC 7300 100) and the spaced syntax
    (/price ABC 7300 100), matching campulse-bot's CommandParser."""

    _CMD_RE = re.compile(r"^([a-zA-Z_]+)(?:@\w+)?")

    @classmethod
    def split(cls, text: str) -> Tuple[Optional[str], str]:
        text = (text or "").strip()
        if not text.startswith("/"):
            return None, ""
        m = cls._CMD_RE.match(text[1:])
        if not m:
            return None, ""
        return m.group(1).lower(), text[1:][m.end():].strip()

    @staticmethod
    def parse_symbol(remainder: str) -> Optional[str]:
        parts = remainder.split()
        if not parts:
            return None
        return parts[0].lstrip("$").upper() or None

    @staticmethod
    def parse_trade_args(remainder: str) -> Optional[Tuple[str, int, int]]:
        parts = remainder.split()
        if len(parts) < 3:
            return None
        ticker = parts[0].lstrip("$").upper()
        try:
            return ticker, int(parts[1]), int(parts[2])
        except ValueError:
            return None


class TelegramBotService:
    def __init__(self, client=telegram_client):
        self.client = client
        self.trade_repo = TradeRepository()
        self.alloc_repo = AllocationRepository()
        self.user_repo = UserRepository()
        self.link_repo = LinkRepository()
        self.pricing = pricing_service_instance
        self.portfolio = PortfolioService(self.trade_repo, self.alloc_repo, self.pricing)
        self.renderer = ChartRenderer(tz_name="Asia/Phnom_Penh")

    def dispatch(self, user_id: str, chat_id: int, full_name: str, text: str) -> None:
        command, remainder = CommandParser.split(text)
        # Data handlers operate on the canonical account, so a linked Telegram id
        # reads/writes its Google portfolio. /start keeps the raw telegram id — it
        # owns the telegram user row (chat_id) and consumes link codes.
        account_id = self.link_repo.get_primary(user_id)
        try:
            is_group = chat_id < 0
            
            if command in ("start", "portfolio", "position", "stock", "top_orders", "top_tickers"):
                if is_group:
                    self.client.send_message(chat_id, "❌ This command is not allowed in group chats.")
                else:
                    if command == "start":
                        self._start(user_id, chat_id, full_name, remainder)
                    elif command == "portfolio":
                        self._portfolio(account_id, chat_id)
                    elif command == "position":
                        self._position(account_id, chat_id, remainder)
                    elif command == "stock":
                        self._stock(account_id, chat_id, remainder)
                    elif command == "top_orders":
                        self._top_orders(account_id, chat_id)
                    elif command == "top_tickers":
                        self._top_tickers(account_id, chat_id)
            elif command in ("buy", "sell"):
                self._trade(account_id, chat_id, command.upper(), remainder)
            elif command == "price":
                self._price(account_id, chat_id, remainder)
            elif command == "show_all":
                self._show_all(account_id, chat_id)
            else:
                self.client.send_message(chat_id, HELP_TEXT)
        except Exception as e:
            logger.error(f"Telegram command '{command}' failed: {e}", exc_info=True)
            self.client.send_message(chat_id, "❌ Something went wrong. Please try again.")

    # --- Handlers ---

    def _start(self, user_id: str, chat_id: int, full_name: str, remainder: str = "") -> None:
        # The only place chat_id is captured — needed for daily reminders.
        self.user_repo.upsert_user(user_id, full_name or f"User {user_id}", chat_id=chat_id)
        # `/start <code>` from the web app's "Connect Telegram" deep link: redeem
        # the one-time code to link this Telegram id to the user's Google account.
        code = remainder.split()[0] if remainder else ""
        if code:
            primary_id = self.link_repo.consume_code(code)
            if not primary_id:
                self.client.send_message(chat_id, "⚠️ That link code is invalid or expired. Generate a new one in the web app.")
                return
            if primary_id == user_id:
                self.client.send_message(chat_id, "That code is for this same Telegram account — nothing to link.")
                return
            # Don't chain: if others already link to this Telegram id, it's a primary.
            if self.link_repo.is_primary_of_others(user_id):
                self.client.send_message(chat_id, "⚠️ This Telegram account is already a primary account and can't be linked as a secondary.")
                return
            self.link_repo.migrate_data(user_id, primary_id)
            self.link_repo.add_link(user_id, primary_id)
            self.client.send_message(
                chat_id,
                "✅ Linked! This Telegram account now shares your web portfolio. "
                "Your /buy, /sell and reports here go to that account.",
            )
            return
        self.client.send_message(chat_id, HELP_TEXT)

    def _price(self, user_id: str, chat_id: int, remainder: str) -> None:
        ticker = CommandParser.parse_symbol(remainder)
        if not ticker:
            self.client.send_message(chat_id, "Usage: /price$ABC")
            return
        res = self.pricing.get_latest_price(ticker)
        price = float(res.price or 0)
        if not price:
            self.client.send_message(chat_id, f"❌ {ticker}: Price not available")
            return
        change = float(res.change or 0) if res.change is not None else 0.0
        direction = (res.change_direction or "equal").lower()
        img = self.renderer.price_card(ticker, price, change, direction)
        emoji = "\U0001F4C8" if direction == "up" else ("\U0001F4C9" if direction == "down" else "➡️")
        self.client.send_photo(chat_id, img, f"{emoji} {ticker} | {price:,.0f} riel")

    def _trade(self, user_id: str, chat_id: int, side: str, remainder: str) -> None:
        args = CommandParser.parse_trade_args(remainder)
        if not args:
            self.client.send_message(chat_id, f"Usage: /{side.lower()}$ABC 7300 100")
            return
        ticker, price, qty = args
        try:
            result = record_trade(self.trade_repo, self.alloc_repo, user_id, ticker, side, price, qty)
        except ValueError as e:
            self.client.send_message(chat_id, f"❌ {e}")
            return

        trade = result["trade"]
        caption = f"✅ {side} confirmed | Seq #{trade['seq']}"
        if side == "SELL":
            caption += f" | P/L: {result['realisedPnl']:+,} riel"
            if result["warning"]:
                caption += "\n(LIFO match failed, but trade saved)"
            elif result["allocations"]:
                matched = [
                    f"#{self._seq_of(a.get('buyTradeId'))} ({a['qtyAllocated']}@{a['buyPrice']:,})"
                    for a in result["allocations"]
                ]
                caption += f"\n\U0001F4E6 Matched: {', '.join(matched)}"
        img = self.renderer.trade_card(ticker, side, price, qty, trade["commission"], trade["seq"])
        self.client.send_photo(chat_id, img, caption)

    def _seq_of(self, trade_id: Optional[str]) -> int:
        t = self.trade_repo.get_trade(trade_id) if trade_id else None
        return t.get("seq", 0) if t else 0

    def _position(self, user_id: str, chat_id: int, remainder: str) -> None:
        ticker = CommandParser.parse_symbol(remainder)
        if not ticker:
            self.client.send_message(chat_id, "Usage: /position ABC")
            return
        pos = self.portfolio.position_detail(user_id, ticker)
        self.client.send_photo(chat_id, self.renderer.position_card(ticker, pos), f"Position: {ticker}")

    def _stock(self, user_id: str, chat_id: int, remainder: str) -> None:
        ticker = CommandParser.parse_symbol(remainder)
        if not ticker:
            self.client.send_message(chat_id, "Usage: /stock ABC")
            return
        trades = self.trade_repo.list_trades(user_id, ticker)
        if not trades:
            self.client.send_message(chat_id, f"❌ No trades found for {ticker}")
            return
        allocs = self.alloc_repo.list_allocations(user_id, ticker)
        buys = [t for t in trades if t["side"] == "BUY"]
        sells = [t for t in trades if t["side"] == "SELL"]

        buy_data = []
        for buy in sorted(buys, key=lambda x: x["price"]):
            allocated = sum(int(a["qtyAllocated"]) for a in allocs if a["buyTradeId"] == buy["tradeId"])
            buy_data.append({"seq": buy["seq"], "qty": buy["qty"], "price": buy["price"],
                             "remaining": buy["qty"] - allocated})
        sell_data = []
        for sell in sells:
            sell_allocs = [a for a in allocs if a["sellTradeId"] == sell["tradeId"]]
            matched = [{"buySeq": self._seq_of(a["buyTradeId"]), "qty": a["qtyAllocated"], "price": a["buyPrice"]}
                       for a in sell_allocs]
            sell_data.append({"seq": sell["seq"], "qty": sell["qty"], "price": sell["price"],
                              "pnl": sum(int(a["realisedPnl"]) for a in sell_allocs), "matched": matched})

        total_bought = sum(t["qty"] for t in buys)
        total_allocated = sum(int(a["qtyAllocated"]) for a in allocs)
        realised = sum(int(a["realisedPnl"]) for a in allocs)
        summary = {"totalBought": total_bought, "totalSold": sum(t["qty"] for t in sells),
                   "remaining": total_bought - total_allocated, "realisedPnl": realised}
        img = self.renderer.stock_detail_card(ticker, buy_data, sell_data, allocs, summary)
        emoji = "\U0001F4C8" if realised >= 0 else "\U0001F4C9"
        self.client.send_photo(
            chat_id, img,
            f"{emoji} {ticker} | P/L: {realised:+,} riel | Remaining: {total_bought - total_allocated}"
        )

    def _portfolio(self, user_id: str, chat_id: int) -> None:
        rows = self.portfolio.portfolio(user_id)
        if not rows:
            self.client.send_message(chat_id, "\U0001F4ED No trades yet.")
            return
        total = sum(int(r.get("realisedPnl", 0)) + int(r.get("unrealisedPnl", 0)) for r in rows)
        emoji = "\U0001F4C8" if total >= 0 else "\U0001F4C9"
        self.client.send_photo(chat_id, self.renderer.portfolio_card(user_id, rows),
                               f"{emoji} Portfolio | Total: {total:+,} riel")

    def _show_all(self, user_id: str, chat_id: int) -> None:
        stocks = self.pricing.get_all_prices()
        if not stocks:
            self.client.send_message(chat_id, "❌ No data.")
            return
            
        tickers = [s.get('ticker') for s in stocks if s.get('ticker')]
        try:
            sparklines = RedisService().get_sparklines_batch(tickers)
        except Exception as e:
            logger.error(f"Failed to fetch sparklines: {e}")
            sparklines = {}
            
        self.client.send_photo(chat_id, self.renderer.all_stocks_card(stocks, sparklines), "\U0001F4C8 Market Overview")

    def _top_orders(self, user_id: str, chat_id: int) -> None:
        ranked = self.portfolio.top_profitable_buy_orders(user_id, limit=5)
        if not ranked:
            self.client.send_message(chat_id, "No realised profits yet.")
            return
        self.client.send_photo(chat_id, self.renderer.rankings_card("TOP ORDERS", ranked, is_tickers=False))

    def _top_tickers(self, user_id: str, chat_id: int) -> None:
        ranked = self.portfolio.top_profitable_tickers(user_id, limit=5)
        if not ranked:
            self.client.send_message(chat_id, "No realised profits yet.")
            return
        self.client.send_photo(chat_id, self.renderer.rankings_card("TOP TICKERS", ranked, is_tickers=True))

    # --- Daily reminder broadcasts ---

    def broadcast(self, text: str) -> int:
        """Send `text` to every user with a stored chat_id. Returns count sent.
        Pages through all users (get_all_users is paginated)."""
        import time
        sent = 0
        offset = 0
        while True:
            batch = self.user_repo.get_all_users(limit=200, offset=offset)
            if not batch:
                break
            for u in batch:
                cid = u.get("chat_id")
                if cid:
                    self.client.send_message(int(cid), text)
                    sent += 1
                    time.sleep(0.05)
            if len(batch) < 200:
                break
            offset += 200
        return sent

    def broadcast_market_overview(self, caption: str) -> int:
        """Fetch market overview and broadcast to all users."""
        import io
        stocks = self.pricing.get_all_prices()
        if not stocks:
            logger.warning("No stocks data available for market overview broadcast.")
            return 0
            
        tickers = [s.get('ticker') for s in stocks if s.get('ticker')]
        try:
            from app.services.redis_service import RedisService
            sparklines = RedisService().get_sparklines_batch(tickers)
        except Exception as e:
            logger.error(f"Failed to fetch sparklines for broadcast: {e}")
            sparklines = {}
            
        photo_bytes = self.renderer.all_stocks_card(stocks, sparklines)
        photo_data = photo_bytes.getvalue()
        
        sent = 0
        offset = 0
        file_id = None
        import time
        
        while True:
            batch = self.user_repo.get_all_users(limit=200, offset=offset)
            if not batch:
                break
            for u in batch:
                cid = u.get("chat_id")
                if cid:
                    try:
                        if file_id:
                            self.client.send_photo(int(cid), file_id, caption)
                        else:
                            returned_id = self.client.send_photo(int(cid), io.BytesIO(photo_data), caption)
                            if returned_id:
                                file_id = returned_id
                        sent += 1
                        time.sleep(0.05)
                    except Exception as e:
                        logger.error(f"Failed to broadcast overview to {cid}: {e}")
            if len(batch) < 200:
                break
            offset += 200
        return sent
