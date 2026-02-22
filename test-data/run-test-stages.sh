#!/bin/bash

# Test data ingestion script
# Posts tournament CSVs to the receiver in stages to simulate tournament progression

RECEIVER_URL="http://127.0.0.1:3031/ingest"
SLEEP_SECONDS=2

post_stage() {
    local stage_name="$1"
    shift
    local paths=("$@")

    # Build JSON array of paths
    local json_paths=$(printf '"%s",' "${paths[@]}")
    json_paths="[${json_paths%,}]"

    local payload=$(cat <<EOF
{
    "paths": ${json_paths},
    "updatePlayers": true,
    "checkExisting": false,
    "dryRun": false
}
EOF
)

    echo "=========================================="
    echo "Stage: $stage_name"
    echo "Paths: ${paths[*]}"
    echo "=========================================="

    response=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "$RECEIVER_URL")

    http_status=$(echo "$response" | grep "HTTP_STATUS:" | cut -d: -f2)
    body=$(echo "$response" | sed '/HTTP_STATUS:/d')

    echo "Response (HTTP $http_status):"
    echo "$body" | head -20
    echo ""
}

echo "Starting test data ingestion..."
echo "Receiver: $RECEIVER_URL"
echo ""

# Stage 1: Initial schedule with partial rosters
post_stage "1 - Schedule with partial rosters" \
    "test-data/carolina-kickoff-2026/stage1-schedule-partial-rosters.csv" \
    "test-data/queen-city-tuneup-2026/stage1-schedule-partial-rosters.csv"

sleep $SLEEP_SECONDS

# Stage 2: Rosters updated
post_stage "2 - Rosters updated" \
    "test-data/carolina-kickoff-2026/stage2-rosters-updated.csv" \
    "test-data/queen-city-tuneup-2026/stage2-rosters-updated.csv"

sleep $SLEEP_SECONDS

# Stage 3: Games in progress
post_stage "3 - Games in progress" \
    "test-data/carolina-kickoff-2026/stage3-games-in-progress.csv" \
    "test-data/queen-city-tuneup-2026/stage3-pool-play-in-progress.csv"

sleep $SLEEP_SECONDS

# Stage 4: Brackets set (Queen City only has this extra stage)
post_stage "4 - Carolina final, Queen City brackets set" \
    "test-data/carolina-kickoff-2026/stage4-final.csv" \
    "test-data/queen-city-tuneup-2026/stage4-brackets-set.csv"

sleep $SLEEP_SECONDS

# Stage 5: Queen City final
post_stage "5 - Queen City final" \
    "test-data/queen-city-tuneup-2026/stage5-final.csv"

echo "=========================================="
echo "Test data ingestion complete!"
echo "=========================================="
