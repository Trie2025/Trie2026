@echo off
chcp 65001 >nul
echo 【注意】Windows 7 请使用 Python 3.8.x，否则无法运行。
python --version

echo 安装依赖...
pip install -r requirements.txt

echo 打包摄像头提供端 provider.exe （无控制台窗口，仅托盘图标）...
pyinstaller --onefile --noconsole --name provider --clean provider.py

echo 打包查看端 viewer.exe ...
pyinstaller --onefile --name viewer --clean viewer.py

echo 完成。可执行文件位于 dist\ 目录。
pause
