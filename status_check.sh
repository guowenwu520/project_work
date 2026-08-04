watch -n 2 '
echo "===== Load ====="
uptime

echo
echo "===== Memory ====="
free -h

echo
echo "===== Worker Counts ====="
printf "Unity:  "
pgrep -u "$USER" -xc ChangeBlindness || true

printf "ffmpeg: "
pgrep -u "$USER" -xc ffmpeg || true

printf "Xvfb:   "
pgrep -u "$USER" -xc Xvfb || true

echo
echo "===== CPU Processes ====="
ps -u "$USER" -eo pid,comm,%cpu,%mem,rss,stat,etime \
  --sort=-%cpu \
  | awk '"'"'$2=="ChangeBlindness" || $2=="ffmpeg" || $2=="Xvfb"'"'"' \
  | head -n 30
'
