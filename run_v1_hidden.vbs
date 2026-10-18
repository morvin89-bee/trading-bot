' Peluncur tanpa jendela untuk bot shadow V1.
' Task Scheduler memanggil berkas ini; wscript MENUNGGU sampai bot selesai
' sehingga task tetap tercatat "berjalan" dan MultipleInstances IgnoreNew
' mencegah dua bot bersamaan. Jendela cmd yang dulu muncul hilang.
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c pythonw -u ""C:\Users\KakiTangan\trading-bot\main_loop.py"" >> ""C:\Users\KakiTangan\trading-bot\v1_loop.log"" 2>&1", 0, True
