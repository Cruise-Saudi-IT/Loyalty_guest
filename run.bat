@echo off
title Aroya Repeat Guests Insights
echo.
echo ============================================================
echo   Aroya - Repeat Guests Insights
echo ============================================================
echo.
echo Starting server...
echo Local URL:    http://localhost:5001
echo Network URLs will be printed below once the server starts.
echo Press Ctrl+C to stop.
echo.
python guest_duplicates.py
pause
