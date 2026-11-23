#!/usr/bin/env bash
# Run as root after loading dtoverlay=pwm,pin=12,func=4 on Raspberry Pi 5.
set -euo pipefail
servo_user=${1:-user}
for chip in /sys/class/pwm/pwmchip*; do
    if [[ $(basename "$(readlink -f "$chip/device")") != 1f00098000.pwm ]]; then
        continue
    fi
    if [[ ! -d "$chip/pwm0" ]]; then
        echo 0 > "$chip/export"
    fi
    channel="$chip/pwm0"
    echo 0 > "$channel/enable"
    echo 0 > "$channel/duty_cycle"
    echo 20000000 > "$channel/period"
    echo normal > "$channel/polarity"
    pinctrl set 12 a0
    for attribute in enable duty_cycle period polarity; do
        chown "$servo_user" "$channel/$attribute"
        chmod 600 "$channel/$attribute"
    done
    exit 0
done
echo 'RP1 PWM0 is unavailable. Enable the pwm overlay first.' >&2
exit 1
