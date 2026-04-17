#!/bin/sh
# run.sh - Entry point for the sample app

echo "==============================="
echo "  Docksmith Sample App"
echo "==============================="
echo ""
echo "Environment:"
echo "  APP_ENV  = $APP_ENV"
echo "  GREETING = $GREETING"
echo ""
echo "Working directory: $(pwd)"
echo "Files here:"
ls -la
echo ""
echo "$GREETING from inside the container!"
echo ""
echo "Container is isolated — writing test file..."
echo "this file should NOT appear on host" > /tmp/isolation_test.txt
echo "Wrote /tmp/isolation_test.txt inside container"
echo ""
echo "Done. Exiting cleanly."
