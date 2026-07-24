#!/usr/bin/env bash
#编译
UNITY_BIN="$HOME/Unity/Hub/Editor/2022.3.58f1c1/Editor/Unity" ./Tools/build_linux.sh
#断点续跑，指定开始的index，跑过的直接跳过
#WORKERS=10 FFMPEG_THREADS=1 RESUME=1 START_INDEX=255488 COUNT=500 USE_XVFB=1 FPS=30 ./Tools/run_dataset.sh
#从头开始跑
WORKERS=10 FFMPEG_THREADS=1 RANDOM_START=1 COUNT=500 USE_XVFB=1 FPS=30 ./Tools/run_dataset.sh
# 服务器上
#!/usr/bin/env bash
UNITY_BIN=/data4/guowenwu/Unity/Hub/Editor/2022.3.58f1c1/Editor/Unity ./Tools/build_linux.sh
#断点续跑，指定开始的index，跑过的直接跳过
#WORKERS=10 FFMPEG_THREADS=1 RESUME=1 START_INDEX=255488 COUNT=500 USE_XVFB=1 FPS=30 ./Tools/run_dataset.sh
#从头开始跑
WORKERS=10 FFMPEG_THREADS=1 RANDOM_START=1 COUNT=500 USE_XVFB=1 FPS=30 ./Tools/run_dataset.sh

