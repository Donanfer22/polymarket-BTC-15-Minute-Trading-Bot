"""
Patch para PolymarketExecutionClient:
- Suporta market buys com valor em USD (não token qty)
- Corrige 'maker address not allowed' usando ClobClient próprio com signature_type=2 + funder
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)
_patch_applied = False


def _make_funder_clob():
    """Cria ClobClient com signature_type=2 e funder — corrige o maker address."""
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

    pk = os.getenv("POLYMARKET_PK", "")
    if pk and not pk.startswith("0x"):
        pk = "0x" + pk
    funder = os.getenv("POLYMARKET_FUNDER", "")

    clob = ClobClient(
        "https://clob.polymarket.com",
        key=pk,
        chain_id=137,
        signature_type=2,
        funder=funder,
    )
    clob.set_api_creds(ApiCreds(
        api_key=os.getenv("POLYMARKET_API_KEY", ""),
        api_secret=os.getenv("POLYMARKET_API_SECRET", ""),
        api_passphrase=os.getenv("POLYMARKET_PASSPHRASE", ""),
    ))
    logger.info(f"[PATCH] ClobClient funder={funder[:12]}... signature_type=2")
    return clob


def apply_market_order_patch():
    global _patch_applied
    if _patch_applied:
        return True

    try:
        from nautilus_trader.adapters.polymarket.execution import PolymarketExecutionClient
        from nautilus_trader.adapters.polymarket.common.symbol import get_polymarket_token_id
        from nautilus_trader.adapters.polymarket.http.conversion import convert_tif_to_polymarket_order_type
        from nautilus_trader.model.enums import OrderSide, order_side_to_str
        from nautilus_trader.common.enums import LogColor
        from py_clob_client.client import MarketOrderArgs, PartialCreateOrderOptions

        _DEFAULT_USD = float(os.getenv("MARKET_BUY_USD", "5.0"))

        async def _patched_submit_market_order(self, command, instrument):
            order = command.order
            order_type = convert_tif_to_polymarket_order_type(order.time_in_force)
            token_id = get_polymarket_token_id(order.instrument_id)
            neg_risk = self._get_neg_risk_for_instrument(instrument)
            options = PartialCreateOrderOptions(neg_risk=neg_risk)

            if order.side == OrderSide.BUY:
                usd_amount = float(os.getenv("MARKET_BUY_USD", str(_DEFAULT_USD)))
                self._log.info(
                    f"[PATCH] BUY ${usd_amount:.2f} USD via funder ClobClient (sig_type=2)",
                    LogColor.MAGENTA,
                )
                args = MarketOrderArgs(
                    token_id=token_id,
                    amount=usd_amount,
                    side=order_side_to_str(order.side),
                    order_type=order_type,
                )
            else:
                if order.is_quote_quantity:
                    self._deny_market_order_quantity(order, "SELL requer base qty")
                    return
                args = MarketOrderArgs(
                    token_id=token_id,
                    amount=float(order.quantity),
                    side=order_side_to_str(order.side),
                    order_type=order_type,
                )

            # ← AQUI está o fix: ClobClient próprio com signature_type=2 + funder
            clob = _make_funder_clob()
            t0 = self._clock.timestamp()
            signed_order = await asyncio.to_thread(
                clob.create_market_order, args, options=options
            )
            self._log.info(
                f"[PATCH] Signed em {self._clock.timestamp()-t0:.3f}s (funder flow)",
                LogColor.BLUE,
            )

            self.generate_order_submitted(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                ts_event=self._clock.timestamp_ns(),
            )
            await self._post_signed_order(order, signed_order)

        PolymarketExecutionClient._submit_market_order = _patched_submit_market_order
        _patch_applied = True
        logger.info("Patch aplicado — signature_type=2 + funder em todas as ordens")
        return True

    except Exception as e:
        logger.error(f"Patch falhou: {e}")
        import traceback; traceback.print_exc()
        return False