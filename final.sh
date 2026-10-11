#!/bin/sh
# Final run: build both hidden briefs, keep the traces, commit and push only the submission files,
# and print the submission form.  Usage:  sh final.sh
# Expects briefs/hidden-1.md, briefs/hidden-1.openapi.json, briefs/hidden-2.md, briefs/hidden-2.openapi.json
cd "$(dirname "$0")" || exit 1
mkdir -p traces submission
for n in 1 2; do
  b="briefs/hidden-$n.md"; t="briefs/hidden-$n.openapi.json"
  if [ ! -f "$b" ] || [ ! -f "$t" ]; then echo "missing $b or $t"; exit 1; fi
  echo "=== building hidden-$n ==="
  caffeinate -i python3 builder/build.py "$b" "$t" 2>&1 | tee "traces/hidden-$n.log"
  if [ ! -s "submission/hidden-$n.workflow.json" ] || grep -q '"workflow_id": null' "submission/hidden-$n.workflow.json"; then
    echo "!!! hidden-$n did not export from Freya. Look at traces/hidden-$n.log, fix the bug, run again."
  fi
done
git add submission/hidden-* traces/hidden-*
git commit -q -m "Hidden brief runs: exported phone agents and builder traces" && git push -q
c=$(git rev-parse --short HEAD)
r=$(git remote get-url origin | sed 's/\.git$//')
cat <<EOF

=== SUBMISSION FORM (send before 20:00 UTC / 4:00 PM New York) ===
{
  "team": "Daniel Joseph",
  "repo": "$r",
  "commit": "$c",
  "run_command": "python3 builder/build.py {brief_path} {tool_api_path}",
  "workflows": {
    "hidden-1": "submission/hidden-1.workflow.json",
    "hidden-2": "submission/hidden-2.workflow.json"
  },
  "traces": {
    "hidden-1": "traces/hidden-1.log",
    "hidden-2": "traces/hidden-2.log"
  }
}
EOF
