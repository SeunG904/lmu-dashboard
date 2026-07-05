@echo off
REM Build a standalone Windows .exe using PyInstaller (like TinyPedal's build)
REM Run this on Windows, inside the project folder, after "pip install -r requirements.txt"

pip install pyinstaller

pyinstaller --noconfirm --onefile --windowed ^
    --name LMU_Dashboard ^
    --add-data "vendor;vendor" ^
    main.py

echo.
echo Build finished. Find LMU_Dashboard.exe inside the "dist" folder.
pause
