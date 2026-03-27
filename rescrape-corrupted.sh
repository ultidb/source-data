#!/bin/bash
#
# Rescrape corrupted tournament files
# Generated on 2026-03-26 to fix race condition bug
#
# This script will re-scrape 31 tournaments that were corrupted by the
# Selenium race condition bug, ensuring fresh data is fetched.
#

set -e  # Exit on error

YEAR=2026
TOTAL=31
COUNT=0

echo "=========================================="
echo "Re-scraping 31 corrupted tournaments"
echo "=========================================="
echo ""

# Array of tournament URLs to re-scrape
URLS=(
  "https://play.usaultimate.org/events/Atlantic-Coast-Open-2026/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/Butler-Spring-Fling-2026/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/Cherry-Blossom-Classic-2026/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/D3-Grand-Prix-2026/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/Florida-Warm-Up-2026/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/Florida-Warm-Up-2026/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/Huckleberry-Flick-2026/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/Kentucky-Hucky/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/Kentucky-Hucky/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/Mardi-Gras-XXXVIII/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/Mid-Atlantic-Warmup-2026/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/Monument-Melee-2026/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/Needle-in-a-Ho-Stack-2026/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/Needle-in-a-Ho-Stack-2026/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/No-Sleep-Till-Stony/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/No-Sleep-Till-Stony/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/Northeast-Classic-2026/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/Old-Capitol-Open-2026/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/PBR-State-Open-2026/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/PLU-Mens-BBQ-2026/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/Presidents-Day-Qualifiers-2026/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/Richmond-is-for-Lovers/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/Richmond-is-for-Lovers/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/Rockford-Meltdown-26/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/Skibidi-Ohio-Rizz-2026/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/Skibidi-Ohio-Rizz-2026/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/Stanford-Open-2026/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/T-Town-Throwdown-2026/schedule/Men/CollegeMen/"
  "https://play.usaultimate.org/events/T-Town-Throwdown-2026/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/Tropical-Toss-UP-2026/schedule/Women/CollegeWomen/"
  "https://play.usaultimate.org/events/UMN-B-Duck-Dome/schedule/Men/CollegeMen/"
)

# Loop through each URL and scrape
for URL in "${URLS[@]}"; do
  COUNT=$((COUNT + 1))
  echo "[$COUNT/$TOTAL] Scraping: $URL"

  # Run the scraper with overwrite and disable-cache flags
  python3 cli.py scrape tournament "$URL" -y $YEAR --overwrite --disable-cache

  echo ""
done

echo "=========================================="
echo "✓ Successfully re-scraped all $TOTAL tournaments"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Review the changes: git status"
echo "2. Verify a few files manually to ensure data is correct"
echo "3. Commit the fixes: git add csv/2026 && git commit -m 'Fix corrupted tournament data'"
