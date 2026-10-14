Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\KakiTangan\trading-bot"
sh.Run "cmd /c ""C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\pythonw.exe"" -u monitor.py >> monitor.log 2>&1", 0, False
