#!/bin/bash

source /scripts/02-common.sh

log_message "RUNNING" "03-install-mono.sh"

# Install Mono if not present
if [ ! -e "/config/.wine/drive_c/windows/mono" ]; then
    log_message "INFO" "Downloading and installing Mono..."
    wget -O /tmp/mono.msi https://dl.winehq.org/wine/wine-mono/9.4.0/wine-mono-9.4.0-x86.msi > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        WINEDLLOVERRIDES=mscoree=d wine msiexec /i /tmp/mono.msi /qn
        if [ $? -eq 0 ]; then
            log_message "INFO" "Mono installed successfully."
        else
            log_message "ERROR" "Failed to install Mono."
        fi
        rm -f /tmp/mono.msi
    else
        log_message "ERROR" "Failed to download Mono installer."
    fi
else
    log_message "INFO" "Mono is already installed."
fi

# Initialize Wine configuration
winecfg  -v=win11
