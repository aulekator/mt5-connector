#!/bin/bash

source /scripts/02-common.sh

log_message "RUNNING" "04-install-mt5.sh"

# Check if MetaTrader 5 is installed
if [ -e "$mt5file" ]; then
    log_message "INFO" "File $mt5file already exists."
else
    log_message "INFO" "File $mt5file is not installed. Installing..."

    # Set Windows 10 mode in Wine and download and install MT5
    $wine_executable reg add "HKEY_CURRENT_USER\\Software\\Wine" /v Version /t REG_SZ /d "win10" /f
    log_message "INFO" "Downloading MT5 installer..."
    wget -O /tmp/mt5setup.exe $mt5setup_url > /dev/null 2>&1
    log_message "INFO" "Installing MetaTrader 5..."
    $wine_executable /tmp/mt5setup.exe /auto
    rm -f /tmp/mt5setup.exe
fi

if [ -f ${MT5_LOGIN_SECRET:-/secrets/servers.dat} ]; then
   log_message "INFO" "found ${MT5_LOGIN_SECRET:-/secrets/servers.dat} . Copying ... "
   cp ${MT5_LOGIN_SECRET:-/secrets/servers.dat} "/config/.wine/drive_c/Program Files/MetaTrader 5/Config/servers.dat"
   log_message "INFO" "found ${MT5_LOGIN_SECRET:-/secrets/servers.dat} Copied "
fi


log_message "INFO" "Template setup.ini with for Account ${MT5_ACCOUNT} and server ${MT5_SERVER}."
if [ -z "${MT5_PASSWORD}"]; then
    log_message "INFO" "Password is not set"
else 
    log_message "INFO" "Password is set"
fi
config=""

if [ -f ${MT5_CONFIG_SECRET:-/secrets/setup.ini} ]; then
   log_message "INFO" "found configuration file ${MT5_CONFIG_SECRET:-/secrets/setup.ini} . appending to params "
   cp ${MT5_CONFIG_SECRET:-/secrets/setup.ini} /config/.wine/drive_c/setup.ini
   config="/config:C:\setup.ini"
   log_message "INFO" "config set to '$config'"
elif [[ -n "${MT5_ACCOUNT}" ]]  && [[ -n "${MT5_PASSWORD}" ]] && [[ -n "${MT5_SERVER}" ]]; then
    log_message "INFO" "Environment variables found, templating .ini file"
    cat << EOF > /config/.wine/drive_c/setup.ini
[Common]
Login=${MT5_ACCOUNT}
Password="${MT5_PASSWORD}"
Server=${MT5_SERVER}
AutoConfiguration=true
ProxyEnable=false

[Charts]
Profile=default

[Experts]
AllowDllImport=1
Enabled=1
WebRequest=1

[StartUp]
Script=ticks_setup
Symbol=EURUSD
Period=M1
EOF
   config="/config:C:\setup.ini"
   log_message "INFO" "config set to '$config'"
else
    log_message "INFO" "no configuration file found and no Environment variables set. metatrader ini files not templated."
fi

# ---- Provision MQL5 sources (mt5ticks) ----
find_mql5_dir() {
    mkdir -p "/config/.wine/drive_c/Program Files/MetaTrader 5/MQL5"
    echo "/config/.wine/drive_c/Program Files/MetaTrader 5/MQL5"
}

if mql5_dir=$(find_mql5_dir); then
    log_message "INFO" "MQL5 dir: $mql5_dir"

    mkdir -p "$mql5_dir/Experts" "$mql5_dir/Scripts" \
             "$mql5_dir/Include/MQL5Book/ws" \
             "$mql5_dir/Files" "$mql5_dir/Profiles/Templates"

    cp /mt5ticks/MQL5/Expert/ticks.mq5        "$mql5_dir/Experts/ticks.mq5"
    cp /mt5ticks/MQL5/Scripts/ticks_setup.mq5 "$mql5_dir/Scripts/ticks_setup.mq5"
    cp /mt5ticks/MQL5/Include/JAson.mqh       "$mql5_dir/Include/JAson.mqh"
    cp -r /mt5ticks/MQL5/Include/MQL5Book/.   "$mql5_dir/Include/MQL5Book/"
    chown -R abc:abc "$mql5_dir"

    # symbols.txt from MT5_SYMBOLS (comma-separated)
    : > "$mql5_dir/Files/symbols.txt"
    if [ -n "$MT5_SYMBOLS" ]; then
        IFS=',' read -r -a syms <<< "$MT5_SYMBOLS"
        printf '%s\n' "${syms[@]}" > "$mql5_dir/Files/symbols.txt"
    fi
    log_message "INFO" "symbols.txt written: $(wc -l < "$mql5_dir/Files/symbols.txt") symbols"

    # ticks.tpl template with the WS Server input baked in.
    # Real MT5 tpl format (verified against the terminal's own saved templates):
    # UTF-16LE with BOM, CRLF line endings, root <chart> container, and the EA in
    # its own <expert> block (name/magic/flags/inputs on separate lines). The
    # chart header keeps the applied chart's own symbol/timeframe (ignores symbol=).
    tpl_utf8=$(mktemp)
    cat > "$tpl_utf8" <<EOF
<chart>
id=20611538696288
symbol=USDJPY
description=US Dollar vs Japanese Yen
period_type=1
period_size=1
digits=3
tick_size=0.000000
position_time=0
scale_fix=0
scale_fixed_min=158.430000
scale_fixed_max=159.610000
scale_fix11=0
scale_bar=0
scale_bar_val=1.000000
scale=16
mode=1
fore=0
grid=1
volume=1
scroll=1
shift=0
shift_size=19.379845
fixed_pos=0.000000
ticker=1
ohlc=0
one_click=0
one_click_btn=1
bidline=1
askline=0
lastline=0
days=0
descriptions=0
tradelines=1
tradehistory=1
window_left=0
window_top=0
window_right=0
window_bottom=0
window_type=1
floating=0
floating_left=0
floating_top=0
floating_right=0
floating_bottom=0
floating_type=1
floating_toolbar=1
floating_tbstate=
background_color=0
foreground_color=16777215
barup_color=65280
bardown_color=65280
bullcandle_color=0
bearcandle_color=16777215
chartline_color=65280
volumes_color=3329330
grid_color=10061943
bidline_color=10061943
askline_color=255
lastline_color=49152
stops_color=255
windows_total=1

<expert>
name=ticks
path=Experts\ticks.ex5
expertmode=5
<inputs>
Server=${MT5_WS_URL:-ws://127.0.0.1:9000}
ReconnectIntervalSec=3
</inputs>
</expert>

<window>
height=100.000000
objects=0

<indicator>
name=Main
path=
apply=1
show_data=1
scale_inherit=0
scale_line=0
scale_line_percent=50
scale_line_value=0.000000
scale_fix_min=0
scale_fix_min_val=0.000000
scale_fix_max=0
scale_fix_max_val=0.000000
expertmode=0
fixed_height=-1
</indicator>
</window>
</chart>                                                        
EOF
    printf '\xff\xfe' > "$mql5_dir/Profiles/Templates/ticks.tpl"
    sed 's/$/\r/' "$tpl_utf8" | iconv -f UTF-8 -t UTF-16LE >> "$mql5_dir/Profiles/Templates/ticks.tpl"
    rm -f "$tpl_utf8"
    # ChartApplyTemplate with a bare name also checks terminal_dir/Profiles/Templates/
    # (doc-literal path); copy there too so either lookup succeeds.
    mkdir -p "/config/.wine/drive_c/Program Files/MetaTrader 5/Profiles/Templates"
    cp "$mql5_dir/Profiles/Templates/ticks.tpl" \
       "/config/.wine/drive_c/Program Files/MetaTrader 5/Profiles/Templates/ticks.tpl"
    chown abc:abc "$mql5_dir/Profiles/Templates/ticks.tpl" \
        "/config/.wine/drive_c/Program Files/MetaTrader 5/Profiles/Templates/ticks.tpl"
    log_message "INFO" "ticks.tpl generated (Server=${MT5_WS_URL:-ws://127.0.0.1:9000})"

    # Compile with MetaEditor (sits next to terminal64.exe)
    # FIX 2026-08-13 (live container verify): binary is MetaEditor64.exe (uppercase M) on the case-sensitive /config
    metaeditor="/config/.wine/drive_c/Program Files/MetaTrader 5/MetaEditor64.exe"
    if [ -f "$metaeditor" ]; then
        log_message "INFO" "Compiling ticks.mq5 + ticks_setup.mq5 ..."
        # FIX 2026-08-13 (live container verify): MetaEditor64.exe only honors the LAST
        # /compile argument and truncates /compile paths containing spaces, so compile
        # each source from a no-space staging dir (C:\mt5build) and deploy the .ex5 next.
        stage="/config/.wine/drive_c/mt5build"
        rm -rf "$stage"
        mkdir -p "$stage/Include"
        cp "$mql5_dir/Experts/ticks.mq5"        "$stage/ticks.mq5"
        cp "$mql5_dir/Scripts/ticks_setup.mq5"  "$stage/ticks_setup.mq5"
        cp "$mql5_dir/Include/JAson.mqh"        "$stage/Include/JAson.mqh"
        cp -r "$mql5_dir/Include/MQL5Book/."    "$stage/Include/MQL5Book/"
        for src in ticks.mq5 ticks_setup.mq5; do
            wine "$metaeditor" "/compile:C:\\mt5build\\$src" "/log:C:\\compile_$src.log"
        done
        for i in $(seq 1 60); do
            [ -f "$stage/ticks.ex5" ] && \
            [ -f "$stage/ticks_setup.ex5" ] && break
            sleep 1
        done
        if [ ! -f "$stage/ticks.ex5" ] || \
           [ ! -f "$stage/ticks_setup.ex5" ]; then
            log_message "ERROR" "MQL5 compile failed - ticks.ex5 / ticks_setup.ex5 missing"
            exit 1
        fi
        cp "$stage/ticks.ex5"        "$mql5_dir/Experts/ticks.ex5"
        cp "$stage/ticks_setup.ex5"  "$mql5_dir/Scripts/ticks_setup.ex5"
        log_message "INFO" "MQL5 compiled: ticks.ex5 + ticks_setup.ex5"
    else
        log_message "ERROR" "MetaEditor64.exe not found next to terminal64.exe"
        exit 1
    fi
else
    log_message "WARNING" "No MT5 data directory found; skipping EA provisioning"
fi

download_servers () {
    xdotool mousemove 400 300 click 1
    sleep 0.5
    xdotool key Alt_R+f
    xdotool key a
    xdotool key Tab
    xdotool key Tab
    xdotool key Tab
    xdotool type ${MT5_SERVER}
    xdotool key Enter
    sleep 5
    xdotool key Escape
}

if [ ! -f "/config/servers.dat.created" ]; then
    log_message "INFO" "servers not downloaded yet. Starting metatrader to download"
    if [ -e "$mt5file" ]; then
        log_message "INFO" "File $mt5file is installed. Running MT5..."
        log_message "INFO" " starting with command $wine_executable $mt5file $config /portable "
        $wine_executable "$mt5file" $config /portable  &
    
    else
        log_message "ERROR" "File $mt5file is not installed. MT5 cannot be run."
    fi
    sleep 5
    download_servers
    touch /config/servers.dat.created
    log_message "INFO" "Servers Downloaded, restarting terminal"
    $wine_executable taskkill /IM terminal64.exe /F
    sleep 0.5
fi

# Recheck if MetaTrader 5 is installed
if [ -e "$mt5file" ]; then
    log_message "INFO" "File $mt5file is installed. Running MT5..."
    log_message "INFO" " starting with command $wine_executable $mt5file $config /portable "
    $wine_executable "$mt5file" $config /portable  &
else
    log_message "ERROR" "File $mt5file is not installed. MT5 cannot be run."
fi
