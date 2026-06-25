#!/bin/bash
set -e

echo "安装依赖..."
pip install -r requirements.txt

echo "打包摄像头提供端 provider （无控制台窗口）..."
pyinstaller --onefile --noconsole --name provider --clean provider.py

echo "打包查看端 viewer ..."
pyinstaller --onefile --name viewer --clean viewer.py

echo "完成。可执行文件位于 dist/ 目录。"
