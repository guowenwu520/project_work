# Command
cd UnitySceneGeneration/ChangeBlindnessRoom/
## Generate videos and video-wise metadata
### Run this before collapse
START_INDEX=454466 COUNT=500 WORKERS=48 RANDOM_RESOLUTION=1 OUTPUT=outz ./Tools/run_dataset_canonical.sh
### Continue to generate when process stuck
WORKERS=12 RESUME=1 START_INDEX=454466 COUNT=500 OUTPUT=outz ./Tools/run_dataset_canonical.sh
WORKERS=8 UNITY_JOB_WORKERS=1 RESUME=1 START_INDEX=454470 COUNT=1 OUTPUT=outz ./Tools/run_dataset_canonical.sh
## rearrange QAs according to preferred metadata format for training and evaluation
python3 ./Tools/regenerate_existing_qa_canonical.py outz --require-all-videos
## split the whole dataset into train and test
python  ./Tools/video_dataset_split_canonical.py split outz/videodata.json datav7/test datav7/train  --count 100 --seed 44


## v1.0
python Tools/regenerate_existing_qa_world_state.py outz --require-all-videos

python  ./Tools/video_dataset_split_world_state.py outz/videodata.json datav7/test datav7/train  --count 100 --seed 44 --json-only
