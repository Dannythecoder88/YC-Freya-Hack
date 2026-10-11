# source env.sh  — exports everything in .env
set -a; . "$(dirname "${BASH_SOURCE[0]:-$0}")/.env"; set +a
