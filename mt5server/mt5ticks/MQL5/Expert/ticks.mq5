//+------------------------------------------------------------------+
//|                                                       ticks.mq5  |
//|            OnTick EA streaming ticks to the WS hub (mt5ticks)   |
//+------------------------------------------------------------------+
#property script_show_inputs

//+------------------------------------------------------------------+
//| I N P U T S                                                      |
//+------------------------------------------------------------------+
input string Server = "ws://127.0.0.1:9000/";
input int ReconnectIntervalSec = 0;

#include <MQL5Book/AutoPtr.mqh>
#include <MQL5Book/ws/wsclient.mqh>
#include <JAson.mqh>

WebSocketClient<Hybi> *wss = NULL;
string symbol;
bool g_helloSent = false;
datetime g_lastReconnect = 0;

//+------------------------------------------------------------------+
//| Announce this EA connection to the hub (once per connect)        |
//+------------------------------------------------------------------+
void SendHello()
{
    if (g_helloSent || wss == NULL || !wss.isConnected())
        return;
    CJAVal hello(jtOBJ, "");
    hello["type"] = "hello";
    hello["role"] = "ea";
    wss.send(hello.Serialize());
    g_helloSent = true;
    Print("Hello Send");
}

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
void OnInit()
{   
    Print("ticks EA initialzing");
    symbol = Symbol();

    wss = new WebSocketClient<Hybi>(Server);
    wss.setTimeOut(10000);
    if (!wss.open())
    {
        Print("Failed to connect to server");
        return;
    }
    SendHello();
    Print("ticks EA initialization successful");
    
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    wss.close();
    delete wss;
    Print("WebSocket client closed");
    Print("Deinitialization");
}

//+------------------------------------------------------------------+
//| onTick method is called every tick                               |
//+------------------------------------------------------------------+
void OnTick()
{
    if (!wss.isConnected() && TimeCurrent() - g_lastReconnect >= ReconnectIntervalSec)
    {
        g_lastReconnect = TimeCurrent();
        Print("Reconnecting");
        g_helloSent = false;
        if (!wss.open())
        {
            Print("Failed to reconnect to server");
        }
        else
        {
            SendHello();
        }
    }

    MqlTick tick;
    if (SymbolInfoTick(symbol, tick))
    {
        CJAVal tickObj(jtOBJ, "");
        tickObj["symbol"] = symbol;
        tickObj["time"] = TimeToString(tick.time, TIME_DATE|TIME_MINUTES|TIME_SECONDS);
        tickObj["ask"] = DoubleToString(tick.ask);
        tickObj["bid"] = DoubleToString(tick.bid);
        tickObj["volume"] = DoubleToString(tick.volume);
        tickObj["last"] = DoubleToString(tick.last);
        tickObj["time_msec"] = IntegerToString(tick.time_msc);
        tickObj["flags"] = IntegerToString(tick.flags);

        string msg = tickObj.Serialize();
        wss.send(msg);
    }
}
