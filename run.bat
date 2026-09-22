@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist data\reservoir.json python tools\generate_data.py
python server.py
