@echo off
python --version
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
pyinstaller --onefile --noconsole --name provider --clean provider.py
pyinstaller --onefile --noconsole --name viewer --clean viewer.py
echo Done. Check dist folder.
pause
