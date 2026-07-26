#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$PROJECT_DIR/Tools/run_dataset.sh"
REGENERATOR="$PROJECT_DIR/Tools/regenerate_existing_qa.py"
QA_LIBRARY="$PROJECT_DIR/Assets/StreamingAssets/tabletop_qa_templates.json"
OUTPUT="${OUTPUT:-$PROJECT_DIR/Output/QA_Coverage48_$(date +%Y%m%d_%H%M%S)}"
FPS="${FPS:-10}"; WORKERS="${WORKERS:-2}"; UNITY_JOB_WORKERS="${UNITY_JOB_WORKERS:-2}"
START_INDEX="${START_INDEX:-$((100000 + $(date +%s) % 700000))}"
EXPECTED_SCHEMA="${EXPECTED_SCHEMA:-six-change-tabletop-8qa-60pool-v7}"
QA_ONLY="${QA_ONLY:-0}"
change_types=(one_object_replacement two_objects_replacement same_object_color_change distance_increase swap_positions no_change)
[[ -f "$QA_LIBRARY" ]] || { echo "Missing QA library: $QA_LIBRARY" >&2; exit 2; }
[[ -f "$REGENERATOR" ]] || { echo "Missing QA regenerator: $REGENERATOR" >&2; exit 2; }
rm -rf "$OUTPUT"; mkdir -p "$OUTPUT/data"
echo "48-scene QA coverage test"
echo "  output: $OUTPUT"
echo "  mode: $([[ "$QA_ONLY" == "1" ]] && echo 'QA-only fast test' || echo 'render 48 videos')"
echo
if [[ "$QA_ONLY" == "1" ]]; then
python3 - "$OUTPUT" "$START_INDEX" "$EXPECTED_SCHEMA" <<'PYFAST'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]);start=int(sys.argv[2]);schema=sys.argv[3]
types=['one_object_replacement','two_objects_replacement','same_object_color_change','distance_increase','swap_positions','no_change']
def state(label,color):return {'label':label,'color':color,'supportsColor':True}
for ti,ct in enumerate(types):
 for si in range(8):
  bid=start+ti*8+si;batch=root/f'Batch_{bid:06d}_{ct}';batch.mkdir(parents=True)
  vp=f'data/video_{bid:06d}.mp4';(root/vp).write_bytes(b'qa-test')
  lb=state(f'item A {si}','red');rb=state(f'item B {si}','blue');la=dict(lb);ra=dict(rb);slot='left' if si%2==0 else 'right'
  if ct=='one_object_replacement':
   if slot=='left':la=state(f'replacement A {si}','green')
   else:ra=state(f'replacement B {si}','yellow')
  elif ct=='two_objects_replacement':la=state(f'replacement A {si}','green');ra=state(f'replacement B {si}','yellow')
  elif ct=='same_object_color_change':
   if slot=='left':la=state(lb['label'],'green')
   else:ra=state(rb['label'],'yellow')
  elif ct=='distance_increase':slot='both'
  elif ct=='swap_positions':la,ra=rb,lb;slot='both'
  else:slot='none'
  a={'schemaVersion':schema,'batchId':bid,'seed':bid*17+3,'changeType':ct,'changedSlot':slot,'leftBefore':lb,'rightBefore':rb,'leftAfter':la,'rightAfter':ra,'videoPath':vp,'qa':[]}
  (batch/'annotation.json').write_text(json.dumps(a,indent=2),encoding='utf-8')
PYFAST
else
 [[ -x "$RUNNER" ]] || { echo "Missing runner: $RUNNER" >&2; exit 2; }
 for ti in "${!change_types[@]}"; do
  ct="${change_types[$ti]}"; type_start=$((START_INDEX+ti*8))
  echo "[$((ti+1))/6] Rendering 8 scenes: $ct"
  OUTPUT="$OUTPUT" START_INDEX="$type_start" COUNT=8 WORKERS="$WORKERS" UNITY_JOB_WORKERS="$UNITY_JOB_WORKERS" FPS="$FPS" RANDOM_START=0 RANDOM_RESOLUTION=0 WIDTH=336 HEIGHT=336 PRESET="${PRESET:-ultrafast}" CRF="${CRF:-28}" CLEAN_OUTPUT=0 RESUME=0 DELETE_FRAMES=1 FORCE_CHANGE_TYPE="$ct" EXPECTED_SCHEMA="$EXPECTED_SCHEMA" "$RUNNER"
 done
fi
python3 "$REGENERATOR" "$OUTPUT" --templates "$QA_LIBRARY" --no-backup --require-all-videos
python3 - "$OUTPUT" "$QA_LIBRARY" "$EXPECTED_SCHEMA" <<'PYVALIDATE'
import json,re,sys
from collections import Counter,defaultdict
from pathlib import Path

root=Path(sys.argv[1])
lib=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
schema=sys.argv[3]
types=[
 "one_object_replacement",
 "two_objects_replacement",
 "same_object_color_change",
 "distance_increase",
 "swap_positions",
 "no_change",
]

def fail(message):
 raise SystemExit("[48-scene user-reviewed QA coverage failed] "+message)

expected={
 group["change_type"]:{item["template_id"] for item in group["templates"]}
 for group in lib["change_types"]
}

for change_type in types:
 if len(expected.get(change_type,set()))!=60:
  fail(f"{change_type}: library count is not 60")

for group in lib["change_types"]:
 for item in group["templates"]:
  raw=(item.get("question","")+" "+item.get("answer",""))
  variables=set(item.get("required_variables") or [])
  forbidden_variables={
   "original_position","position_a","position_b","position_1","position_2",
   "selected_position","reference_object","reference_object_1",
   "reference_object_2","reference_object_a","reference_object_b",
   "relative_position","relative_position_1","relative_position_2",
   "relative_position_a","relative_position_b",
   "initial_relative_position","final_relative_position",
  }
  bad=variables & forbidden_variables
  if bad:
   fail(f"{item['template_id']}: obsolete variables {sorted(bad)}")
  for phrase in [
   "physical table position that appears",
   "initial-view orientation",
   "initial-view left",
   "initial-view right",
   "final-view left",
   "final-view right",
   "reference object",
   "left (1st view)",
   "right (1st view)",
   "left (2nd view)",
   "right (2nd view)",
  ]:
   if phrase in raw.lower():
    fail(f"{item['template_id']}: obsolete phrase {phrase}")

anns=sorted(root.glob("Batch_*/annotation.json"))
if len(anns)!=48:
 fail(f"expected 48 annotations, found {len(anns)}")

scenes=Counter()
appearances=defaultdict(Counter)
allowed_labels={
 "the left side of the table in the first view",
 "the right side of the table in the first view",
 "the left side of the table in the second view",
 "the right side of the table in the second view",
}

def validate_left_right(text, location):
 lowered=text.lower()
 stripped=lowered
 for label in allowed_labels:
  stripped=stripped.replace(label, "")
 if re.search(r"\bleft\b|\bright\b", stripped):
  fail(f"{location}: nonstandard left/right wording: {text}")

for path in anns:
 annotation=json.loads(path.read_text(encoding="utf-8"))
 change_type=annotation.get("changeType")
 scenes[change_type]+=1
 if change_type not in types:
  fail(f"{path.parent.name}: bad change type {change_type}")
 if annotation.get("schemaVersion")!=schema:
  fail(f"{path.parent.name}: schema mismatch")

 qa=annotation.get("qa")
 ids=annotation.get("qaTemplateIds")
 if not isinstance(qa,list) or len(qa)!=8:
  fail(f"{path.parent.name}: QA count")
 if not isinstance(ids,list) or len(ids)!=8 or len(set(ids))!=8:
  fail(f"{path.parent.name}: template-id count")

 questions=[item.get("question","").strip() for item in qa]
 if len(set(questions))!=8:
  fail(f"{path.parent.name}: duplicate questions")

 for pair in qa:
  question=pair.get("question","").strip()
  answer=pair.get("answer","").strip()
  combined=question+" "+answer
  if re.search(r"\{[A-Za-z0-9_]+\}",combined):
   fail(f"{path.parent.name}: unresolved placeholder")
  validate_left_right(question,path.parent.name+" question")
  validate_left_right(answer,path.parent.name+" answer")
  for old in [
   "after the camera returned",
   "after the camera returns",
   "camera returned to",
   "physical table position that appears",
   "initial-view orientation",
   "reference object",
   "tabletop state",
   "initial state",
   "second state",
  ]:
   if old in combined.lower():
    fail(f"{path.parent.name}: outdated wording {old}")

 for template_id in ids:
  if template_id not in expected[change_type]:
   fail(f"{path.parent.name}: unknown id {template_id}")
  appearances[change_type][template_id]+=1

for change_type in types:
 if scenes[change_type]!=8:
  fail(f"{change_type}: expected 8 scenes, got {scenes[change_type]}")
 missing=expected[change_type]-set(appearances[change_type])
 if missing:
  fail(f"{change_type}: missing {sorted(missing)}")
 distribution=Counter(appearances[change_type].values())
 if distribution!=Counter({1:56,2:4}):
  fail(f"{change_type}: occurrence distribution {dict(distribution)}")

records=json.loads((root/"videodata.json").read_text(encoding="utf-8"))
if len(records)!=48:
 fail("videodata count is not 48")

print("\n[48-scene user-reviewed QA coverage validation passed]")
for change_type in types:
 print(f"  {change_type}: 60/60 covered; 56 once, 4 twice")
print("  left/right wording: natural first-view / second-view phrasing passed")
print("  obsolete reference-object wording: none")
print("  output: "+str(root))
PYVALIDATE
echo; echo "Coverage test complete: $OUTPUT"
