#!/bin/sh
# Final run:  sh final.sh <case_folder> <mock_url>
# Builds the phone agent from the case folder against our mock, keeps the trace, commits and pushes the
# workflow export, the trace and the mock source, and writes submission.json.
cd "$(dirname "$0")" || exit 1
folder="$1"; mock="$2"
if [ ! -d "$folder" ] || [ -z "$mock" ]; then echo "usage: sh final.sh <case_folder> <mock_url>"; exit 1; fi
mkdir -p traces submission
rm -f submission/final.workflow.json          # never pair this run's trace with an older export
echo "=== mock health: $(curl -s "$mock/health")"
caffeinate -i python3 builder/build.py "$folder" "$mock" 2>&1 | tee traces/final.log
if ! grep -q "BUILD END status=ok" traces/final.log || [ ! -s submission/final.workflow.json ] || grep -q '"workflow_id": null' submission/final.workflow.json; then
  echo "!!! the agent was not exported from Freya. Read traces/final.log, fix the bug, run again."; exit 1
fi
c_prev=$(git rev-parse --short HEAD)
r=$(git remote get-url origin | sed 's/\.git$//')
git add submission/final.workflow.json traces/final.log traces/final-*.log mock/
git commit -q -m "Final case: exported phone agent, builder trace, mock" && git push -q
c=$(git rev-parse --short HEAD)
cat > submission.json <<EOF
{
  "team": "Daniel Joseph",
  "members": [
    {"name": "Daniel Joseph", "email": "daniel.joseph6514@gmail.com"}
  ],
  "repo": "$r",
  "commit": "$c",
  "run_command": "python3 builder/build.py {case_folder} {mock_url}",
  "workflow": "submission/final.workflow.json",
  "trace": "traces/final.log",
  "mock_url": "$mock"
}
EOF
git add submission.json && git commit -q -m "submission.json" && git push -q
echo; echo "=== submission.json (send before 20:00 UTC / 4:00 PM New York) ==="; cat submission.json
