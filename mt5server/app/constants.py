from enum import Enum
import MetaTrader5 as mt5

class MT5Timeframe(Enum):
    M1 = mt5.TIMEFRAME_M1       # 1-minute
    M5 = mt5.TIMEFRAME_M5       # 5-minute
    M15 = mt5.TIMEFRAME_M15     # 15-minute
    M30 = mt5.TIMEFRAME_M30     # 30-minute
    H1 = mt5.TIMEFRAME_H1       # 1-hour
    H4 = mt5.TIMEFRAME_H4       # 4-hour
    D1 = mt5.TIMEFRAME_D1       # daily
    W1 = mt5.TIMEFRAME_W1       # weekly
    MN1 = mt5.TIMEFRAME_MN1     # monthly

TRADE_RETCODE_DESCRIPTION = {
    mt5.TRADE_RETCODE_REQUOTE: "Requote",
    mt5.TRADE_RETCODE_REJECT: "Request rejected",
    mt5.TRADE_RETCODE_CANCEL: "Request canceled by trader",
    mt5.TRADE_RETCODE_PLACED: "Order placed",
    mt5.TRADE_RETCODE_DONE: "Request completed",
    mt5.TRADE_RETCODE_DONE_PARTIAL: "Only part of the request was completed",
    mt5.TRADE_RETCODE_ERROR: "Request processing error",
    mt5.TRADE_RETCODE_TIMEOUT: "Request canceled by timeout",
    mt5.TRADE_RETCODE_INVALID: "Invalid request",
    mt5.TRADE_RETCODE_INVALID_VOLUME: "Invalid volume in the request",
    mt5.TRADE_RETCODE_INVALID_PRICE: "Invalid price in the request",
    mt5.TRADE_RETCODE_INVALID_STOPS: "Invalid stops in the request",
    mt5.TRADE_RETCODE_TRADE_DISABLED: "Trade is disabled",
    mt5.TRADE_RETCODE_MARKET_CLOSED: "Market is closed",
    mt5.TRADE_RETCODE_NO_MONEY: "There is not enough money to complete the request",
    mt5.TRADE_RETCODE_PRICE_CHANGED: "Prices changed",
    mt5.TRADE_RETCODE_PRICE_OFF: "There are no quotes to process the request",
    mt5.TRADE_RETCODE_INVALID_EXPIRATION: "Invalid order expiration date in the request",
    mt5.TRADE_RETCODE_ORDER_CHANGED: "Order state changed",
    mt5.TRADE_RETCODE_TOO_MANY_REQUESTS: "Too frequent requests",
    mt5.TRADE_RETCODE_NO_CHANGES: "No changes in request",
    mt5.TRADE_RETCODE_SERVER_DISABLES_AT: "Autotrading disabled by server",
    mt5.TRADE_RETCODE_CLIENT_DISABLES_AT: "Autotrading disabled by client terminal",
    mt5.TRADE_RETCODE_LOCKED: "Request locked for processing",
    mt5.TRADE_RETCODE_FROZEN: "Order or position frozen",
    mt5.TRADE_RETCODE_INVALID_FILL: "Invalid order filling type",
    mt5.TRADE_RETCODE_CONNECTION: "No connection with the trade server",
    mt5.TRADE_RETCODE_ONLY_REAL: "Operation is allowed only for live accounts",
    mt5.TRADE_RETCODE_LIMIT_ORDERS: "The number of pending orders has reached the limit",
    mt5.TRADE_RETCODE_LIMIT_VOLUME: "The volume of orders and positions for the symbol has reached the limit",
    mt5.TRADE_RETCODE_INVALID_ORDER: "Incorrect or prohibited order type",
    mt5.TRADE_RETCODE_POSITION_CLOSED: "Position with the specified POSITION_IDENTIFIER has already been closed",
    mt5.TRADE_RETCODE_INVALID_CLOSE_VOLUME: "A close volume exceeds the current position volume",
    mt5.TRADE_RETCODE_CLOSE_ORDER_EXIST: "A close order already exists for a specified position",
    mt5.TRADE_RETCODE_LIMIT_POSITIONS: "The number of open positions simultaneously present on an account can be limited by the server settings",
    mt5.TRADE_RETCODE_REJECT_CANCEL: "The pending order activation request is rejected, the order is canceled",
    mt5.TRADE_RETCODE_LONG_ONLY: "The request is rejected, because the 'Only long positions are allowed' rule is set for the symbol",
    mt5.TRADE_RETCODE_SHORT_ONLY: "The request is rejected, because the 'Only short positions are allowed' rule is set for the symbol",
    mt5.TRADE_RETCODE_CLOSE_ONLY: "The request is rejected, because the 'Only position closing is allowed' rule is set for the symbol",
    mt5.TRADE_RETCODE_FIFO_CLOSE: "The request is rejected, because 'Position closing is allowed only by FIFO rule' flag is set for the trading account",
}