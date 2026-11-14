[Setup]
AppName=Avena Smart Weighing POS
AppVersion=1.0
DefaultDirName={autopf}\AvenaSmartPOS
DefaultGroupName=Avena Smart POS
OutputBaseFilename=AvenaSmartPOS_Setup
[Files]
Source: "dist\AvenaSmartPOS.exe"; DestDir: "{app}"
[Icons]
Name: "{group}\Avena Smart POS"; Filename: "{app}\AvenaSmartPOS.exe"
Name: "{commondesktop}\Avena Smart POS"; Filename: "{app}\AvenaSmartPOS.exe"
