//+------------------------------------------------------------------+
//|                                        ticks_setup.mq5           |
//| Startup script: open one M1 chart per symbol from symbols.txt    |
//| and attach the ticks EA via ticks.tpl. A symbol is skipped only  |
//| if a chart for it is already open AND its ticks EA is running,   |
//| so charts are opened only once across terminal restarts. No      |
//| includes — compiles with a bare MetaEditor.                      |
//+------------------------------------------------------------------+
#property script_show_inputs

input string SymbolsFile  = "symbols.txt";
input string TemplateFile = "ticks.tpl";
input string ExpertName   = "ticks";

// Saved chart templates are inspected via the MQL5/Files sandbox.
const string TempTemplate = "/tmp/ticks_setup_check";

//+------------------------------------------------------------------+
//| Script program start function                                    |
//+------------------------------------------------------------------+
void OnStart()
{
    string symbols[];
    if (!ReadSymbolsFile(SymbolsFile, symbols))
    {
        Print("ticks_setup: no symbols to stream");
        return;
    }

    int opened = 0;
    int repaired = 0;
    int skipped = 0;
    bool is_false = false;

    for (int i = 0; i < ArraySize(symbols); i++)
    {
        string symbol = symbols[i];

        if (!SymbolExist(symbol, is_false))
        {
            PrintFormat("ticks_setup: unknown symbol '%s' - skipping", symbol);
            skipped++;
            continue;
        }


        if (!SymbolSelect(symbol, true))
        {
            PrintFormat("ticks_setup: fialed to select symbol '%s' - skipping", symbol);
            skipped++;
            continue;
        }

        long chart = FindChart(symbol);
        if (chart != 0)
        {
            if (ExpertName != "" && ChartHasExpert(chart, ExpertName))
            {
                PrintFormat("ticks_setup: '%s' already set up - skipping", symbol);
                skipped++;
                continue;
            }

            if (!ChartApplyTemplate(chart, TemplateFile))
            {
                PrintFormat("ticks_setup: ChartApplyTemplate failed for '%s'", symbol);
                skipped++;
                continue;
            }
            repaired++;
            continue;
        }
        chart = ChartOpen(symbol, PERIOD_M1);
        if (chart == 0)
        {
            PrintFormat("ticks_setup: ChartOpen failed for '%s' - skipping", symbol);
            PrintFormat("ChartOpen() returned %d. LastError %d", chart, GetLastError());
            skipped++;
            continue;
        }

        if (!ChartApplyTemplate(chart, TemplateFile))
        {
            PrintFormat("ticks_setup: ChartApplyTemplate failed for '%s'", symbol);
            skipped++;
            continue;
        }
        opened++;
    }

    // close the setup chart
    PrintFormat("ticks_setup: done - charts=%d repaired=%d skipped=%d", opened, repaired, skipped);
    ChartClose(ChartID());
}

//+------------------------------------------------------------------+
//| Returns the handle of the first open chart for 'symbol', or 0.   |
//+------------------------------------------------------------------+
long FindChart(const string symbol)
{
    long chart = ChartFirst();
    while (chart != -1)
    {

        if (ChartSymbol(chart) == symbol && chart != ChartID())
            return chart;
        chart = ChartNext(chart);
    }
    return 0;
}

//+------------------------------------------------------------------+
//| True when the chart currently runs the EA 'expertName'.          |
//| The chart's current settings are saved to a temp template in the |
//| Files sandbox and inspected for a matching <expert> section.     |
//+------------------------------------------------------------------+
bool ChartHasExpert(const long chart, const string expertName)
{
    FileDelete(TempTemplate + ".tpl");
    if (!ChartSaveTemplate(chart, TempTemplate))
        return false;
    Sleep(100); // let the terminal finish writing the template file

    int handle = FileOpen(TempTemplate + ".tpl", FILE_READ | FILE_TXT);
    if (handle == INVALID_HANDLE)
        return false;

    bool inExpert = false;
    bool found = false;
    while (!FileIsEnding(handle) && !found)
    {
        string line = FileReadString(handle);
        StringTrimLeft(line);
        StringTrimRight(line);

        if (line == "<expert>")
        {
            inExpert = true;
            continue;
        }
        if (line == "</expert>")
        {
            inExpert = false;
            continue;
        }
        if (inExpert)
        {
            string pair[];
            if (StringSplit(line, '=', pair) >= 2)
            {
                if (pair[0] == "name" && pair[1] == expertName)
                    found = true;
                else if (pair[0] == "path" && StringFind(pair[1], expertName) >= 0)
                    found = true;
            }
        }
    }
    FileClose(handle);
    FileDelete(TempTemplate + ".tpl");
    return found;
}

//+------------------------------------------------------------------+
//| Read one symbol per line; trims, drops empties and '#' comments. |
//+------------------------------------------------------------------+
bool ReadSymbolsFile(const string filename, string &outSymbols[])
{
    int handle = FileOpen(filename, FILE_READ | FILE_TXT | FILE_ANSI);
    if (handle == INVALID_HANDLE)
    {
        PrintFormat("ticks_setup: cannot open '%s' (err=%d)", filename, GetLastError());
        return false;
    }

    ArrayResize(outSymbols, 0);
    while (!FileIsEnding(handle))
    {
        string line = FileReadString(handle);
        StringTrimLeft(line);
        StringTrimRight(line);
        if (StringLen(line) > 0 && StringFind(line, "#") != 0)
        {
            int size = ArraySize(outSymbols);
            ArrayResize(outSymbols, size + 1);
            outSymbols[size] = line;
        }
    }
    FileClose(handle);

    return ArraySize(outSymbols) > 0;
}
