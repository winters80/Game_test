@echo off
rem Double-click to play: starts Ollama, pulls missing models, runs the game.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
if errorlevel 1 pause
