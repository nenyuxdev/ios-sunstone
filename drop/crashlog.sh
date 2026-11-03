#!/bin/sh
# Print crash reports to the kernel console so they appear on the phone screen.
#
# Installed by the boot image's SWEETDRP archive as /usr/local/sweet-crashlog.sh
# and started by a launchd job at boot. The kernel console is the only channel
# this port has: no serial, no adb while XNU runs, and the persistent log ring
# needs a recovery with adb that a stock MIUI recovery does not provide.
LOG=/private/var/mobile/Library/Logs/CrashReporter
[ -d "$LOG" ] || LOG=/var/mobile/Library/Logs/CrashReporter

echo "=== crashlog: watching $LOG" > /dev/console
last=""
n=0
while [ $n -lt 900 ]; do
	newest=$(ls -t "$LOG"/*.ips 2>/dev/null | head -1)
	if [ -n "$newest" ] && [ "$newest" != "$last" ]; then
		echo "" > /dev/console
		echo "=== CRASHLOG $newest ===" > /dev/console
		# The .ips header is one JSON line followed by the payload JSON.
		sed -n '1,120p' "$newest" > /dev/console 2>/dev/null
		echo "=== END CRASHLOG ===" > /dev/console
		last=$newest
	fi
	n=$((n + 1))
	sleep 2
done
