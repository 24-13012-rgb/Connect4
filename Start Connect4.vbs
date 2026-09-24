Set objShell = CreateObject("WScript.Shell")
strPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strPath

' Start Flask completely hidden (window style 0 = hidden, False = don't wait)
objShell.Run "python app.py", 0, False

' Give the server a moment to start up
WScript.Sleep 1500

' Open the game in the default browser
objShell.Run "cmd /c start http://127.0.0.1:5000", 0, True
